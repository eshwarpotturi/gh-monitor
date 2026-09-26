#!/usr/bin/env python3
"""
End-to-end test: fetches the last N real events from configured targets,
generates AI summaries via Gemini, and sends an email.

Usage:
  python test_summary.py [count]              — last N events (default: 2)
  python test_summary.py --commit owner/repo sha  — test a specific commit

Required env vars:
  GEMINI_API_KEY  — from repo secrets (injected by GitHub Actions)
  EMAIL_PASSWORD  — Gmail app password
Optional:
  GH_TOKEN        — GitHub token for higher API rate limits
"""

import os
import sys
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor import (
    load_json, CONFIG_FILE, GITHUB_API,
    target_to_url, fetch_events,
    describe_event, event_url, enrich_push_event,
    fetch_commit_details, build_ai_prompt, call_gemini,
    get_ai_summary,
    build_body, send_email,
)


def test_specific_commit(repo_full, sha, gemini_key, github_token=""):
    """Fetch a specific commit, summarise it with Gemini, print the result."""
    print(f"\nFetching commit {sha[:12]} from {repo_full} ...")

    # Build a minimal fake PushEvent for this commit
    headers = {"Accept": "application/vnd.github.v3+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    resp = requests.get(f"{GITHUB_API}/repos/{repo_full}/commits/{sha}",
                        headers=headers, timeout=15)
    if resp.status_code != 200:
        print(f"  Could not fetch commit: HTTP {resp.status_code}")
        return

    data    = resp.json()
    message = data.get("commit", {}).get("message", "")
    files   = data.get("files", [])

    # Build a unified diff string from the patch fields
    diff_parts = []
    for f in files:
        diff_parts.append(f"--- {f['filename']} (+{f['additions']} -{f['deletions']})")
        if f.get("patch"):
            diff_parts.append(f['patch'][:5000])
    diff = "\n".join(diff_parts)

    fake_evt = {
        "type": "PushEvent",
        "actor": {"login": repo_full.split("/")[0]},
        "repo":  {"name": repo_full},
        "payload": {
            "ref":     "refs/heads/master",
            "size":    1,
            "commits": [{"sha": sha, "message": message}],
        },
    }

    prompt = build_ai_prompt(fake_evt, diff)
    print(f"\nCommit message : {message}")
    print(f"Files changed  : {len(files)}")
    print()

    summary = call_gemini(prompt, gemini_key)
    print("=== GEMINI SUMMARY ===")
    print(summary or "(no summary returned)")
    print("======================")


def main():
    cfg          = load_json(CONFIG_FILE, {})
    token        = os.environ.get("GH_TOKEN", "")
    models_token = os.environ.get("GEMINI_API_KEY", "")

    # Mode: test a specific commit
    if len(sys.argv) >= 4 and sys.argv[1] == "--commit":
        repo_full = sys.argv[2]
        sha       = sys.argv[3]
        test_specific_commit(repo_full, sha, models_token, token)
        return

    # Mode: last N events
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2

    env_targets = os.environ.get("MONITOR_TARGETS", "")
    raw_targets = [t.strip() for t in env_targets.split(",") if t.strip()] if env_targets else cfg.get("targets", [])

    targets = {}
    for raw in raw_targets:
        url, label = target_to_url(raw)
        if url:
            targets[label] = url

    if not targets:
        print("No valid targets found. Set MONITOR_TARGETS env var or edit config.json.")
        raise SystemExit(1)

    target_label = ", ".join(targets.keys())
    print(f"Fetching last {count} event(s) from: {target_label}")
    print(f"AI summaries: {'yes (Gemini)' if models_token else 'NO — GEMINI_API_KEY not set'}")

    all_raw = []
    for label, url in targets.items():
        all_raw.extend(fetch_events(url, token) or [])

    all_raw.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    recent = all_raw[:count]
    recent.reverse()

    if not recent:
        print("No events found.")
        raise SystemExit(1)

    events_for_email = []
    for evt in recent:
        etype = evt.get("type", "?")
        repo  = evt.get("repo", {}).get("name", "")
        print(f"  Summarising: {etype} in {repo} ...")

        enrich_push_event(evt, token)
        ai_summary = get_ai_summary(evt, models_token, api_token=token)
        if ai_summary:
            print(f"    → {ai_summary[:100]}...")
        else:
            print(f"    → (no AI summary — using rule-based fallback)")

        events_for_email.append({
            "id":         evt.get("id", ""),
            "time":       evt.get("created_at", "?"),
            "repo":       repo,
            "described":  describe_event(evt),
            "ai_summary": ai_summary,
            "url":        event_url(evt),
        })

    subject = f"[GitHub Monitor — TEST] Last {count} event(s) from {target_label}"
    body    = build_body(events_for_email, target_label)
    body   += "\n\n─────────────────────────────────────────────────────────────────\nThis is a TEST email verifying the end-to-end pipeline."

    send_email(subject, body)
    cfg_to = cfg.get("email_to", os.environ.get("EMAIL_TO", ""))
    print(f"\nDone. Test email sent to {cfg_to}")


if __name__ == "__main__":
    main()
