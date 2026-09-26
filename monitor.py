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

CONFIG_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
STATE_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
GITHUB_API    = "https://api.github.com"
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


# ── Plain-English event descriptions ─────────────────────────────────────────

def describe_event(evt):
    """
    Returns a dict with:
      label       — short category name (e.g. "Code Update")
      headline    — one sentence saying what happened, in plain English
      details     — list of bullet-point strings with specifics
      meaning     — one sentence explaining what this type of event means
    """
    etype   = evt.get("type", "")
    actor   = evt.get("actor", {}).get("login", "?")
    repo    = evt.get("repo",  {}).get("name",  "?").split("/")[-1]  # just repo name, not owner/repo
    payload = evt.get("payload", {})

    def branch():
        return payload.get("ref", "?").replace("refs/heads/", "")

    def pr():
        p = payload.get("pull_request", {})
        return p.get("title", "untitled"), p.get("number", "?"), p.get("state", "")

    def issue():
        i = payload.get("issue", {})
        return i.get("title", "untitled"), i.get("number", "?")

    if etype == "PushEvent":
        commits = payload.get("commits", [])
        count   = payload.get("size", len(commits))
        bullets = [f'  • "{c.get("message","").splitlines()[0]}"' for c in commits[:5]]
        if count > 5:
            bullets.append(f"  … and {count - 5} more change(s)")
        return {
            "label":    "Code Update",
            "headline": f'@{actor} pushed {count} new change(s) to the "{branch()}" branch of "{repo}".',
            "details":  bullets,
            "meaning":  "New code was added or modified in this project — could be a bug fix, new feature, or other improvement.",
        }

    if etype == "PullRequestEvent":
        title, num, _ = pr()
        action = payload.get("action", "?")
        merged = payload.get("pull_request", {}).get("merged", False)
        if action == "closed" and merged:
            verb    = "merged (accepted and applied)"
            meaning = "The proposed changes were approved and are now a permanent part of the project."
        elif action == "closed":
            verb    = "closed without merging"
            meaning = "The proposal was rejected or withdrawn — those changes will not be applied."
        elif action == "opened":
            verb    = "opened"
            meaning = "A pull request is a proposal to add changes. It is under review and not live yet."
        else:
            verb    = action
            meaning = "A pull request is a proposal to add changes to a project."
        return {
            "label":    "Pull Request",
            "headline": f'@{actor} {verb} a pull request in "{repo}".',
            "details":  [f'  Title: "{title}"  (PR #{num})'],
            "meaning":  meaning,
        }

    if etype == "PullRequestReviewEvent":
        title, num, _ = pr()
        state   = payload.get("review", {}).get("state", "?")
        state_readable = {"approved": "approved", "changes_requested": "requested changes on", "commented": "commented on"}.get(state, state)
        return {
            "label":    "Code Review",
            "headline": f'@{actor} {state_readable} a pull request in "{repo}".',
            "details":  [f'  Pull request: "{title}"  (PR #{num})'],
            "meaning":  "A code review is when someone examines proposed changes and gives feedback before they are merged.",
        }

    if etype == "PullRequestReviewCommentEvent":
        title, num, _ = pr()
        return {
            "label":    "Review Comment",
            "headline": f'@{actor} left a comment during code review in "{repo}".',
            "details":  [f'  Pull request: "{title}"  (PR #{num})'],
            "meaning":  "A line-by-line comment on proposed code changes, usually asking a question or suggesting an improvement.",
        }

    if etype == "IssuesEvent":
        title, num = issue()
        action = payload.get("action", "?")
        meanings = {
            "opened": "An issue is used to report bugs, request features, or track tasks. This one is now open for discussion.",
            "closed": "This issue has been resolved — the bug was fixed, the feature was added, or it was decided no action is needed.",
            "reopened": "This issue was previously closed but has been reopened — the problem may have come back or wasn't fully resolved.",
        }
        return {
            "label":    "Issue " + action.capitalize(),
            "headline": f'@{actor} {action} an issue in "{repo}".',
            "details":  [f'  Title: "{title}"  (Issue #{num})'],
            "meaning":  meanings.get(action, "Issues are used to track bugs, feature requests, and tasks."),
        }

    if etype == "IssueCommentEvent":
        title, num = issue()
        return {
            "label":    "Issue Comment",
            "headline": f'@{actor} commented on an issue in "{repo}".',
            "details":  [f'  Issue: "{title}"  (Issue #{num})'],
            "meaning":  "Someone replied to an ongoing discussion about a bug, feature, or task.",
        }

    if etype == "CreateEvent":
        ref_type = payload.get("ref_type", "?")
        ref      = payload.get("ref") or payload.get("master_branch", "?")
        meanings = {
            "repository": "A new project has been created on GitHub.",
            "branch":     "A branch is a separate workspace — developers use them to work on a feature or fix without affecting the main code.",
            "tag":        "A tag marks a specific point in the project's history, often used to label a version like v1.0.",
        }
        return {
            "label":    f"New {ref_type.capitalize()} Created",
            "headline": f'@{actor} created a new {ref_type} called "{ref}" in "{repo}".',
            "details":  [],
            "meaning":  meanings.get(ref_type, f"A new {ref_type} was created in this project."),
        }

    if etype == "DeleteEvent":
        ref_type = payload.get("ref_type", "?")
        ref      = payload.get("ref", "?")
        return {
            "label":    f"{ref_type.capitalize()} Deleted",
            "headline": f'@{actor} deleted the {ref_type} "{ref}" in "{repo}".',
            "details":  [],
            "meaning":  f"A {ref_type} that is no longer needed was removed from the project.",
        }

    if etype == "WatchEvent":
        return {
            "label":    "Star",
            "headline": f'@{actor} starred the "{repo}" project.',
            "details":  [],
            "meaning":  'Starring is like bookmarking — it means someone finds the project interesting or useful.',
        }

    if etype == "ForkEvent":
        fork_name = payload.get("forkee", {}).get("full_name", "?")
        return {
            "label":    "Fork",
            "headline": f'@{actor} made a personal copy (fork) of "{repo}".',
            "details":  [f"  Their copy: {fork_name}"],
            "meaning":  "Forking creates an independent copy of a project. People do this to experiment, customise, or contribute changes back.",
        }

    if etype == "ReleaseEvent":
        release = payload.get("release", {})
        tag     = release.get("tag_name", "?")
        name    = release.get("name", "")
        action  = payload.get("action", "published")
        return {
            "label":    "New Release",
            "headline": f'@{actor} {action} a new release of "{repo}": version {tag}.',
            "details":  [f'  Release name: "{name}"'] if name else [],
            "meaning":  "A release is an official, versioned snapshot of the project — like shipping a new version of an app.",
        }

    if etype == "CommitCommentEvent":
        return {
            "label":    "Commit Comment",
            "headline": f'@{actor} commented on a specific code change in "{repo}".',
            "details":  [],
            "meaning":  "A comment left directly on a line of code that was previously committed.",
        }

    if etype == "GollumEvent":
        pages = [p.get("title", "?") for p in payload.get("pages", [])[:3]]
        return {
            "label":    "Wiki Updated",
            "headline": f'@{actor} updated the wiki in "{repo}".',
            "details":  [f'  Page(s) changed: {", ".join(pages)}'] if pages else [],
            "meaning":  "The project's documentation wiki was edited.",
        }

    if etype == "MemberEvent":
        member = payload.get("member", {}).get("login", "?")
        action = payload.get("action", "?")
        return {
            "label":    "Collaborator Change",
            "headline": f'@{actor} {action} @{member} as a collaborator on "{repo}".',
            "details":  [],
            "meaning":  "Collaborators are people who have been granted permission to contribute directly to a project.",
        }

    if etype == "PublicEvent":
        return {
            "label":    "Repo Made Public",
            "headline": f'@{actor} made "{repo}" publicly visible.',
            "details":  [],
            "meaning":  "This project was previously private and is now open for anyone to view.",
        }

    # Fallback for unknown event types
    return {
        "label":    etype.replace("Event", ""),
        "headline": f'@{actor} performed an action in "{repo}".',
        "details":  [],
        "meaning":  "",
    }


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

def fmt_time(iso):
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.strftime("%-d %b %Y, %-I:%M %p UTC")
    except Exception:
        return iso

def build_body(events, target_label, last_notif_ts=None):
    now_str = datetime.now(timezone.utc).strftime("%-d %b %Y, %-I:%M %p UTC")
    total   = len(events)
    divider = "─" * 65

    if last_notif_ts:
        mins = max(1, int((time.time() - last_notif_ts) / 60))
        header = (
            f"You have {total} new update(s) from {target_label} on GitHub.\n"
            f"(These are changes since your last alert {mins} minute(s) ago.)\n"
            f"Sent: {now_str}"
        )
    else:
        header = (
            f"You have {total} new update(s) from {target_label} on GitHub.\n"
            f"Sent: {now_str}"
        )

    lines = [header, ""]

    for i, evt in enumerate(events, 1):
        d = evt["described"]
        lines.append(divider)
        lines.append(f"[{i} of {total}]  {d['label']}  ·  {evt['repo']}  ·  {fmt_time(evt['time'])}")
        lines.append("")
        lines.append(d["headline"])
        if d["details"]:
            lines.append("")
            for bullet in d["details"]:
                lines.append(bullet)
        if d["meaning"]:
            lines.append("")
            lines.append(f"  What this means:  {d['meaning']}")
        lines.append(f"  View on GitHub:   {evt['url']}")
        lines.append("")

    lines.append(divider)
    lines.append(f"Monitoring: {target_label}  |  Sent by gh-monitor")
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
                        "id":         eid,
                        "time":       evt.get("created_at", "?"),
                        "repo":       evt.get("repo", {}).get("name", label),
                        "described":  describe_event(evt),
                        "url":        event_url(evt),
                    })

    if is_first_run:
        print(f"Primed with {len(seen_ids)} existing event IDs. No email sent on first run.")
    elif new_events:
        new_events.sort(key=lambda x: x["time"])
        within_window = last_notif_ts and (time.time() - last_notif_ts < RECENT_WINDOW)
        subject = (
            f"[GitHub Update] {len(new_events)} new update(s) from {target_label}"
            if within_window
            else f"[GitHub Alert] New activity from {target_label}"
        )
        body = build_body(new_events, target_label, last_notif_ts if within_window else None)
        send_email(subject, body)
        print(f"Email sent  : {len(new_events)} event(s)")
        last_notif_ts = time.time()
    else:
        print("No new events.")

    state["seen_ids"]      = list(seen_ids)[-600:]
    state["last_notif_ts"] = last_notif_ts
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
