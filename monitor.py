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

GEMINI_API    = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
GEMINI_SYSTEM = (
    "You summarize GitHub activity for someone who does not write code. "
    "Write 2-4 plain English sentences. Follow this structure:\n"
    "1. What was added or changed (be specific — name the actual file, feature, or topic).\n"
    "2. What it does or what it is about (explain it like you would to a curious friend).\n"
    "3. Why it might matter or be interesting (optional, only if obvious from the content).\n"
    "Rules: no jargon (no 'commit', 'branch', 'diff', 'PR', 'repo', 'merge', 'push'). "
    "Use the actual content — titles, descriptions, filenames — not generic phrases like 'files were updated'."
)


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

def fetch_commit_diff(repo_full, sha, token=""):
    """Fetch the unified diff for a single commit. Returns truncated diff string."""
    headers = {"Accept": "application/vnd.github.v3.diff"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo_full}/commits/{sha}",
            headers=headers, timeout=15
        )
        if resp.status_code == 200:
            diff = resp.text
            if len(diff) > 4000:
                diff = diff[:4000] + "\n... (diff truncated)"
            return diff
        print(f"  Diff HTTP {resp.status_code}: {repo_full}/{sha[:7]}")
    except requests.RequestException as exc:
        print(f"  Diff fetch error: {exc}")
    return ""


# ── AI Summaries (GitHub Models) ──────────────────────────────────────────────

def build_ai_prompt(evt, diff=""):
    """
    Build the user-message string for the Models API.
    Returns "" for event types where AI adds no value (WatchEvent, ForkEvent, etc.).
    Pure function — no network calls.
    """
    etype   = evt.get("type", "")
    actor   = evt.get("actor", {}).get("login", "?")
    repo    = evt.get("repo",  {}).get("name",  "?").split("/")[-1]
    payload = evt.get("payload", {})

    if etype == "PushEvent":
        branch  = payload.get("ref", "").replace("refs/heads/", "")
        commits = payload.get("commits", [])
        count   = payload.get("size", len(commits))
        messages = [c.get("message", "").splitlines()[0] for c in commits[:5] if c.get("message")]
        if messages:
            bullets = "\n".join(f"{i}. {m}" for i, m in enumerate(messages, 1))
        else:
            bullets = f"({count} commit(s) — messages not available in event payload)"
        parts = [
            f"Project: {repo}",
            f"Branch:  {branch}",
            f"Number of commits: {count}",
            f"Commit messages:\n{bullets}",
        ]
        if diff:
            parts.append(f"\nActual code diff:\n{diff}")
        return "\n".join(parts)

    if etype == "PullRequestEvent":
        pr     = payload.get("pull_request", {})
        body   = (pr.get("body") or "")[:1500]
        return (
            f"Project: {repo}\n"
            f"Action:  {payload.get('action','')}\n"
            f"Title:   {pr.get('title','')}\n"
            f"Description: {body}\n"
            f"Files changed: {pr.get('changed_files',0)}, "
            f"+{pr.get('additions',0)} / -{pr.get('deletions',0)} lines"
        )

    if etype == "IssuesEvent":
        issue = payload.get("issue", {})
        body  = (issue.get("body") or "")[:1500]
        return (
            f"Project: {repo}\n"
            f"Action:  {payload.get('action','')}\n"
            f"Title:   {issue.get('title','')}\n"
            f"Description: {body}"
        )

    if etype == "IssueCommentEvent":
        issue   = payload.get("issue", {})
        comment = payload.get("comment", {})
        return (
            f"Project: {repo}\n"
            f"Issue:   {issue.get('title','')}\n"
            f"Comment: {(comment.get('body') or '')[:1500]}"
        )

    if etype == "ReleaseEvent":
        release = payload.get("release", {})
        notes   = (release.get("body") or "")[:1500]
        return (
            f"Project: {repo}\n"
            f"Version: {release.get('tag_name','')}\n"
            f"Name:    {release.get('name','')}\n"
            f"Notes:   {notes}"
        )

    if etype == "PullRequestReviewEvent":
        pr     = payload.get("pull_request", {})
        review = payload.get("review", {})
        return (
            f"Project: {repo}\n"
            f"PR:      {pr.get('title','')}\n"
            f"Review state: {review.get('state','')}\n"
            f"Review body:  {(review.get('body') or '')[:1000]}"
        )

    # WatchEvent, ForkEvent, CreateEvent, DeleteEvent, MemberEvent, etc.
    return ""


def call_gemini(prompt, api_key):
    """POST to Gemini API. Returns summary string or "" on any failure."""
    try:
        resp = requests.post(
            f"{GEMINI_API}?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{
                    "role": "user",
                    "parts": [{"text": f"{GEMINI_SYSTEM}\n\n{prompt}"}],
                }],
                "generationConfig": {
                    "maxOutputTokens": 400,
                    "temperature": 0.4,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data       = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()
                finish = candidates[0].get("finishReason", "unknown")
                print(f"  Gemini no text — finishReason: {finish}")
        else:
            print(f"  Gemini {resp.status_code}: {resp.text[:120]}")
    except Exception as exc:
        print(f"  Gemini error: {exc}")
    return ""


def get_ai_summary(evt, models_token, api_token=""):
    """
    Entry point for AI summarisation.
    Fetches commit diff for PushEvents, builds prompt, calls Gemini.
    Returns "" if AI is skipped or fails — caller uses rule-based fallback.
    """
    if not models_token:
        return ""

    diff = ""
    if evt.get("type") == "PushEvent":
        repo_full = evt.get("repo", {}).get("name", "")
        commits   = evt.get("payload", {}).get("commits", [])
        if commits:
            sha  = commits[-1].get("sha", "")
            # api_token is a GitHub token — never pass the Gemini key here
            diff = fetch_commit_diff(repo_full, sha, api_token)

    prompt = build_ai_prompt(evt, diff)
    if not prompt:
        return ""

    return call_gemini(prompt, models_token)


# ── Plain-English event descriptions ─────────────────────────────────────────

def describe_event(evt):
    """
    Returns a dict:
      label    — short category  (e.g. "Code Update")
      headline — one sentence saying what happened
      details  — list of bullet strings with specifics
      meaning  — fallback meaning used when AI is unavailable
    """
    etype   = evt.get("type", "")
    actor   = evt.get("actor", {}).get("login", "?")
    repo    = evt.get("repo",  {}).get("name",  "?").split("/")[-1]
    payload = evt.get("payload", {})

    def branch():
        return payload.get("ref", "?").replace("refs/heads/", "")

    def pr():
        p = payload.get("pull_request", {})
        return p.get("title", "untitled"), p.get("number", "?")

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
            "meaning":  "New code was added or modified in this project.",
        }

    if etype == "PullRequestEvent":
        title, num = pr()
        action = payload.get("action", "?")
        merged = payload.get("pull_request", {}).get("merged", False)
        if action == "closed" and merged:
            verb    = "merged (accepted and applied)"
            meaning = "The proposed changes were approved and are now a permanent part of the project."
        elif action == "closed":
            verb    = "closed without merging"
            meaning = "The proposal was rejected or withdrawn."
        else:
            verb    = action
            meaning = "A pull request is a proposal to add changes — it's under review and not live yet."
        return {
            "label":    "Pull Request",
            "headline": f'@{actor} {verb} a pull request in "{repo}".',
            "details":  [f'  Title: "{title}"  (PR #{num})'],
            "meaning":  meaning,
        }

    if etype == "PullRequestReviewEvent":
        title, num = pr()
        state = payload.get("review", {}).get("state", "?")
        readable = {"approved": "approved", "changes_requested": "requested changes on", "commented": "commented on"}.get(state, state)
        return {
            "label":    "Code Review",
            "headline": f'@{actor} {readable} a pull request in "{repo}".',
            "details":  [f'  Pull request: "{title}"  (PR #{num})'],
            "meaning":  "A code review is when someone examines proposed changes and gives feedback.",
        }

    if etype == "PullRequestReviewCommentEvent":
        title, num = pr()
        return {
            "label":    "Review Comment",
            "headline": f'@{actor} left a comment during code review in "{repo}".',
            "details":  [f'  Pull request: "{title}"  (PR #{num})'],
            "meaning":  "A line-by-line comment on proposed code changes.",
        }

    if etype == "IssuesEvent":
        title, num = issue()
        action = payload.get("action", "?")
        meanings = {
            "opened":   "An issue is used to report bugs or request features — this one is now open for discussion.",
            "closed":   "This issue has been resolved.",
            "reopened": "This issue was previously closed but has been reopened.",
        }
        return {
            "label":    f"Issue {action.capitalize()}",
            "headline": f'@{actor} {action} an issue in "{repo}".',
            "details":  [f'  Title: "{title}"  (Issue #{num})'],
            "meaning":  meanings.get(action, "Issues are used to track bugs and feature requests."),
        }

    if etype == "IssueCommentEvent":
        title, num = issue()
        return {
            "label":    "Issue Comment",
            "headline": f'@{actor} commented on an issue in "{repo}".',
            "details":  [f'  Issue: "{title}"  (Issue #{num})'],
            "meaning":  "Someone replied to an ongoing discussion about a bug or feature.",
        }

    if etype == "CreateEvent":
        ref_type = payload.get("ref_type", "?")
        ref      = payload.get("ref") or payload.get("master_branch", "?")
        meanings = {
            "repository": "A new project has been created on GitHub.",
            "branch":     "A branch is a separate workspace for working on a feature or fix.",
            "tag":        "A tag marks a specific version in the project's history.",
        }
        return {
            "label":    f"New {ref_type.capitalize()} Created",
            "headline": f'@{actor} created a new {ref_type} called "{ref}" in "{repo}".',
            "details":  [],
            "meaning":  meanings.get(ref_type, f"A new {ref_type} was created."),
        }

    if etype == "DeleteEvent":
        ref_type = payload.get("ref_type", "?")
        ref      = payload.get("ref", "?")
        return {
            "label":    f"{ref_type.capitalize()} Deleted",
            "headline": f'@{actor} deleted the {ref_type} "{ref}" in "{repo}".',
            "details":  [],
            "meaning":  f"A {ref_type} that is no longer needed was removed.",
        }

    if etype == "WatchEvent":
        return {
            "label":    "Star",
            "headline": f'@{actor} starred the "{repo}" project.',
            "details":  [],
            "meaning":  'Starring is like bookmarking — it means someone finds the project interesting.',
        }

    if etype == "ForkEvent":
        fork_name = payload.get("forkee", {}).get("full_name", "?")
        return {
            "label":    "Fork",
            "headline": f'@{actor} made a personal copy (fork) of "{repo}".',
            "details":  [f"  Their copy: {fork_name}"],
            "meaning":  "Forking creates an independent copy of a project to experiment with or contribute to.",
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
            "meaning":  "A release is an official, versioned snapshot of the project.",
        }

    if etype == "CommitCommentEvent":
        return {
            "label":    "Commit Comment",
            "headline": f'@{actor} commented on a specific code change in "{repo}".',
            "details":  [],
            "meaning":  "A comment left directly on a previously submitted line of code.",
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
            "meaning":  "Someone was added or removed as a contributor to this project.",
        }

    if etype == "PublicEvent":
        return {
            "label":    "Repo Made Public",
            "headline": f'@{actor} made "{repo}" publicly visible.',
            "details":  [],
            "meaning":  "This project was previously private and is now open for anyone to view.",
        }

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
            f"(Changes since your last alert {mins} minute(s) ago.)\n"
            f"Sent: {now_str}"
        )
    else:
        header = (
            f"You have {total} new update(s) from {target_label} on GitHub.\n"
            f"Sent: {now_str}"
        )

    lines = [header, ""]

    for i, evt in enumerate(events, 1):
        d       = evt["described"]
        # AI summary replaces the generic fallback meaning when available
        meaning = evt.get("ai_summary") or d["meaning"]

        lines.append(divider)
        lines.append(f"[{i} of {total}]  {d['label']}  ·  {evt['repo']}  ·  {fmt_time(evt['time'])}")
        lines.append("")
        lines.append(d["headline"])

        if d["details"]:
            lines.append("")
            for bullet in d["details"]:
                lines.append(bullet)

        if meaning:
            lines.append("")
            lines.append(f"  What this means:  {meaning}")

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
    models_token  = os.environ.get("GEMINI_API_KEY", "")
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
    print(f"Monitoring    : {target_label}")
    print(f"Known IDs     : {len(seen_ids)}")
    print(f"First run     : {is_first_run}")
    print(f"AI summaries  : {'yes (Gemini)' if models_token else 'no (GEMINI_API_KEY not set)'}")

    new_events = []

    for label, url in targets.items():
        for evt in fetch_events(url, token):
            eid = evt.get("id")
            if not eid:
                continue
            if eid not in seen_ids:
                seen_ids.add(eid)
                if not is_first_run:
                    print(f"  New event: {evt.get('type')} in {evt.get('repo',{}).get('name','')}")
                    ai_summary = get_ai_summary(evt, models_token, api_token=token)
                    new_events.append({
                        "id":         eid,
                        "time":       evt.get("created_at", "?"),
                        "repo":       evt.get("repo", {}).get("name", label),
                        "described":  describe_event(evt),
                        "ai_summary": ai_summary,
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
        print(f"Email sent    : {len(new_events)} event(s)")
        last_notif_ts = time.time()
    else:
        print("No new events.")

    state["seen_ids"]      = list(seen_ids)[-600:]
    state["last_notif_ts"] = last_notif_ts
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
