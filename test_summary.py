#!/usr/bin/env python3
"""
End-to-end test: fetches the last N real events from configured targets,
generates AI summaries via GitHub Models, and sends an email.

Usage: python test_summary.py [count]   (default: 2)

Required env vars (same as the main workflow):
  GITHUB_MODELS_TOKEN — auto-provided by GitHub Actions via github.token
  EMAIL_PASSWORD      — Gmail app password (from repo secrets)
Optional:
  GH_TOKEN            — personal access token for higher API rate limits
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor import (
    load_json, CONFIG_FILE,
    target_to_url, fetch_events,
    describe_event, event_url,
    get_ai_summary,
    build_body, send_email,
)

def main():
    count        = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    cfg          = load_json(CONFIG_FILE, {})
    token        = os.environ.get("GH_TOKEN", "")
    models_token = os.environ.get("GITHUB_MODELS_TOKEN", "")

    targets = {}
    for raw in cfg.get("targets", []):
        url, label = target_to_url(raw)
        if url:
            targets[label] = url

    if not targets:
        print("No targets in config.json.")
        raise SystemExit(1)

    target_label = ", ".join(targets.keys())
    print(f"Fetching last {count} event(s) from: {target_label}")
    print(f"AI summaries: {'yes' if models_token else 'NO — GITHUB_MODELS_TOKEN not set'}")

    # Collect all recent events across all targets
    all_raw = []
    for label, url in targets.items():
        all_raw.extend(fetch_events(url, token))

    # Sort newest-first, take the N most recent
    all_raw.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    recent = all_raw[:count]
    recent.reverse()  # oldest-first for email readability

    if not recent:
        print("No events found.")
        raise SystemExit(1)

    # Build event dicts with AI summaries
    events_for_email = []
    for evt in recent:
        etype = evt.get("type", "?")
        repo  = evt.get("repo", {}).get("name", "")
        print(f"  Summarising: {etype} in {repo} ...")

        ai_summary = get_ai_summary(evt, models_token, api_token=token)
        if ai_summary:
            print(f"    → {ai_summary[:80]}...")
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

    cfg_to    = cfg.get("email_to", os.environ.get("EMAIL_TO", ""))
    print(f"\nDone. Test email sent to {cfg_to}")


if __name__ == "__main__":
    main()
