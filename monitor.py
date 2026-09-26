#!/usr/bin/env python3
"""
GitHub Monitor — run-once mode, designed for GitHub Actions.

State (seen event IDs + last notification time) is persisted in state.json,
which GitHub Actions caches between runs.
"""

import os
import json
import time
import smtplib
import requests
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
STATE_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
GITHUB_API  = "https://api.github.com"
RECENT_WINDOW = 600  # seconds — label email as "Update" if notified within this window


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ── GitHub API ────────────────────────────────────────────────────────────────

def fetch_events(url, token=None):
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        print(f"  HTTP {resp.status_code}: {url}")
    except requests.RequestException as exc:
        print(f"  Request error: {exc}")
    return []

def target_to_url(raw):
    """'sanand0/plan' or 'sanand0' or full GitHub URL → (api_url, label)."""
    t = raw.strip().rstrip("/")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    parts = [p for p in t.split("/") if p]
    if len(parts) == 2:
        return f"{GITHUB_API}/repos/{parts[0]}/{parts[1]}/events", f"{parts[0]}/{parts[1]}"
    if len(parts) == 1:
        return f"{GITHUB_API}/users/{parts[0]}/events/public", parts[0]
    print(f"  Unrecognised target: {raw!r} — skipping.")
    return None, None


# ── Event descriptions ────────────────────────────────────────────────────────

def describe_event(evt):
    etype   = evt.get("type", "")
    actor   = evt.get("actor", {}).get("login", "?")
    payload = evt.get("payload", {})

    def commits_text():
        lines = []
        for c in payload.get("commits", [])[:5]:
            msg = c.get("message", "").splitlines()[0]
            sha = c.get("sha", "")[:7]
            lines.append(f"    • {msg}  [{sha}]")
        extra = payload.get("size", 0) - 5
        if extra > 0:
            lines.append(f"    … and {extra} more commit(s)")
        return "\n".join(lines)

    def pr():
        p = payload.get("pull_request", {})
        return f"PR #{p.get('number','?')}: \"{p.get('title','untitled')}\""

    def issue():
        i = payload.get("issue", {})
        return f"Issue #{i.get('number','?')}: \"{i.get('title','untitled')}\""

    mapping = {
        "PushEvent":                     lambda: f"PUSH by @{actor} → {payload.get('ref','?').replace('refs/heads/','')}\n" + commits_text(),
        "PullRequestEvent":              lambda: f"PULL REQUEST {payload.get('action','?').upper()} by @{actor}\n    {pr()}",
        "PullRequestReviewEvent":        lambda: f"PR REVIEW ({payload.get('review',{}).get('state','?')}) by @{actor}\n    {pr()}",
        "PullRequestReviewCommentEvent": lambda: f"PR REVIEW COMMENT by @{actor}\n    {pr()}",
        "IssuesEvent":                   lambda: f"ISSUE {payload.get('action','?').upper()} by @{actor}\n    {issue()}",
        "IssueCommentEvent":             lambda: f"ISSUE COMMENT by @{actor}\n    {issue()}",
        "CreateEvent":                   lambda: f"CREATED {payload.get('ref_type','?')} '{payload.get('ref') or payload.get('master_branch','?')}' by @{actor}",
        "DeleteEvent":                   lambda: f"DELETED {payload.get('ref_type','?')} '{payload.get('ref','?')}' by @{actor}",
        "ForkEvent":                     lambda: f"FORK by @{actor} → {payload.get('forkee',{}).get('full_name','?')}",
        "WatchEvent":                    lambda: f"STARRED by @{actor}",
        "ReleaseEvent":                  lambda: f"RELEASE {payload.get('action','?').upper()} by @{actor}: {payload.get('release',{}).get('tag_name','?')} — \"{payload.get('release',{}).get('name','')}\"",
        "CommitCommentEvent":            lambda: f"COMMIT COMMENT by @{actor}",
        "GollumEvent":                   lambda: f"WIKI UPDATED by @{actor}: " + ", ".join(p.get("title","?") for p in payload.get("pages",[])[:3]),
        "MemberEvent":                   lambda: f"MEMBER {payload.get('action','?').upper()}: @{payload.get('member',{}).get('login','?')} (by @{actor})",
        "PublicEvent":                   lambda: f"REPO MADE PUBLIC by @{actor}",
    }
    fn = mapping.get(etype, lambda: f"{etype} by @{actor}")
    try:
        return fn()
    except Exception:
        return f"{etype} by @{actor}"

def event_url(evt):
    payload = evt.get("payload", {})
    repo    = evt.get("repo", {}).get("name", "")
    base    = f"https://github.com/{repo}"
    etype   = evt.get("type", "")
    try:
        if etype == "PushEvent":
            commits = payload.get("commits", [])
            if commits:
                return f"{base}/commit/{commits[-1]['sha']}"
        elif etype in ("PullRequestEvent", "PullRequestReviewEvent", "PullRequestReviewCommentEvent"):
            n = payload.get("pull_request", {}).get("number")
            if n:
                return f"{base}/pull/{n}"
        elif etype in ("IssuesEvent", "IssueCommentEvent"):
            n = payload.get("issue", {}).get("number")
            if n:
                return f"{base}/issues/{n}"
        elif etype == "ReleaseEvent":
            tag = payload.get("release", {}).get("tag_name")
            if tag:
                return f"{base}/releases/tag/{tag}"
    except Exception:
        pass
    return base


# ── Email ─────────────────────────────────────────────────────────────────────

def build_body(events, target_label, last_notif_ts=None):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines   = []

    if last_notif_ts:
        mins = max(1, int((time.time() - last_notif_ts) / 60))
        lines.append(f"GitHub Update  |  {now_str}")
        lines.append(f"Monitoring : {target_label}")
        lines.append(f"Showing    : {len(events)} new event(s) — delta from last alert {mins} min ago")
    else:
        lines.append(f"GitHub Alert  |  {now_str}")
        lines.append(f"Monitoring : {target_label}")
        lines.append(f"Showing    : {len(events)} new event(s)")

    lines.append("")
    lines.append("=" * 65)

    for evt in events:
        ts = evt["time"].replace("T", " ").replace("Z", " UTC")
        lines.append(f"\n[{ts}]  {evt['repo']}")
        lines.append(evt["description"])
        lines.append(f"  → {evt['url']}")

    lines.append("\n" + "=" * 65)
    return "\n".join(lines)

def send_email(subject, body):
    cfg            = load_json(CONFIG_FILE, {})
    email_from     = os.environ.get("EMAIL_FROM",     cfg.get("email_from", ""))
    email_to       = os.environ.get("EMAIL_TO",       cfg.get("email_to",   ""))
    email_password = os.environ.get("EMAIL_PASSWORD", "")
    smtp_server    = os.environ.get("SMTP_SERVER",    "smtp.gmail.com")
    smtp_port      = int(os.environ.get("SMTP_PORT",  "465"))

    if not email_password:
        raise RuntimeError("EMAIL_PASSWORD secret is not set.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = email_from
    msg["To"]      = email_to
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL(smtp_server, smtp_port) as s:
        s.login(email_from, email_password)
        s.sendmail(email_from, email_to, msg.as_string())


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cfg   = load_json(CONFIG_FILE, {})
    state = load_json(STATE_FILE,  {"seen_ids": [], "last_notif_ts": None})

    seen_ids      = set(state.get("seen_ids", []))
    last_notif_ts = state.get("last_notif_ts")
    token         = os.environ.get("GH_TOKEN", "")
    is_first_run  = len(seen_ids) == 0

    targets = {}
    for raw in cfg.get("targets", []):
        url, label = target_to_url(raw)
        if url:
            targets[label] = url

    if not targets:
        print("No valid targets in config.json. Exiting.")
        raise SystemExit(1)

    target_label = ", ".join(targets.keys())
    print(f"Monitoring  : {target_label}")
    print(f"Known IDs   : {len(seen_ids)}")
    print(f"First run   : {is_first_run}")

    new_events = []

    for label, url in targets.items():
        for evt in fetch_events(url, token):
            eid = evt.get("id")
            if not eid:
                continue
            if eid not in seen_ids:
                seen_ids.add(eid)
                if not is_first_run:
                    new_events.append({
                        "id":          eid,
                        "time":        evt.get("created_at", "?"),
                        "repo":        evt.get("repo", {}).get("name", label),
                        "description": describe_event(evt),
                        "url":         event_url(evt),
                    })

    if is_first_run:
        print(f"Primed with {len(seen_ids)} existing event IDs. No email sent on first run.")
    elif new_events:
        new_events.sort(key=lambda x: x["time"])
        within_window = last_notif_ts and (time.time() - last_notif_ts < RECENT_WINDOW)
        subject = (
            f"[GitHub Update] {len(new_events)} new event(s) — {target_label}"
            if within_window
            else f"[GitHub Alert] Activity detected — {target_label}"
        )
        body = build_body(new_events, target_label, last_notif_ts if within_window else None)
        send_email(subject, body)
        print(f"Email sent  : {len(new_events)} event(s)")
        last_notif_ts = time.time()
    else:
        print("No new events.")

    # Cap seen_ids to avoid unbounded growth
    state["seen_ids"]      = list(seen_ids)[-600:]
    state["last_notif_ts"] = last_notif_ts
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
