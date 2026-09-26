# gh-monitor

Watches a GitHub account's public activity and emails you a plain-English summary whenever something new happens — no developer knowledge required to understand the alerts.

## What it monitors

Any public activity by the accounts listed in `config.json`:

- Code pushed to a project
- Pull requests opened, reviewed, or merged
- Issues created or closed
- New releases published
- Repos starred or forked
- Wiki edits, collaborator changes, and more

## What the email looks like

```
You have 2 new update(s) from sanand0 on GitHub.
Sent: 26 Sep 2026, 1:00 PM UTC

─────────────────────────────────────────────────────────────────
[1 of 2]  Code Update  ·  sanand0/blog  ·  26 Sep 2026, 12:58 PM UTC

@sanand0 pushed 2 new change(s) to the "main" branch of "blog".

  • "Add dark mode support to homepage"
  • "Fix CSS alignment on mobile"

  What this means:  New code was added or modified in this project.
  View on GitHub:   https://github.com/sanand0/blog/commit/abc1234

─────────────────────────────────────────────────────────────────
[2 of 2]  Pull Request  ·  sanand0/tools  ·  26 Sep 2026, 1:00 PM UTC

@sanand0 opened a pull request in "tools".

  Title: "Fix pagination bug on search results"  (PR #42)

  What this means:  A pull request is a proposal to add changes.
                    It is under review and not live yet.
  View on GitHub:   https://github.com/sanand0/tools/pull/42
```

Every event includes:
- **What happened** — in plain English
- **What it means** — a one-liner explaining the type of activity for those unfamiliar with GitHub
- **A direct link** to view it on GitHub

## How it works

A GitHub Actions workflow runs every 5 minutes, calls the GitHub API, and emails you if anything new is found. It remembers what it has already reported so you never get duplicate alerts. If two bursts of activity happen within 10 minutes, the second email is labelled "Update" and tells you how long since the last alert.

## Setup

### 1. Fork or clone this repo into your own GitHub account

### 2. Edit `config.json`

Add the GitHub usernames or public repos you want to watch:

```json
{
  "targets": [
    "some-username",
    "some-username/specific-repo"
  ],
  "email_from": "your.email@gmail.com",
  "email_to":   "your.email@gmail.com"
}
```

> Only **public** repositories and **public** user activity can be monitored. Private repos will return a 404 and be skipped.

### 3. Add secrets

Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|---|---|
| `EMAIL_FROM` | Gmail address to send alerts from |
| `EMAIL_TO` | Email address to receive alerts |
| `EMAIL_PASSWORD` | Gmail App Password (not your real password — see below) |
| `GH_TOKEN` | *(Optional)* GitHub personal access token — increases API rate limits |

**Getting a Gmail App Password:**
1. Go to [myaccount.google.com](https://myaccount.google.com) → Security → 2-Step Verification
2. Scroll to **App passwords** and create one (name it anything, e.g. "gh-monitor")
3. Copy the 16-character password shown and paste it as the `EMAIL_PASSWORD` secret

### 4. Activate the workflow

GitHub doesn't auto-run scheduled workflows on new repos. Go to **Actions → GitHub Monitor → Run workflow** once to kick it off. After that it runs automatically every 5 minutes.

### 5. Send a test email

Go to **Actions → Test Email → Run workflow** to verify your email credentials are working before waiting for real activity.

## Files

| File | Purpose |
|---|---|
| `monitor.py` | Main script — fetches events, formats the email, sends it |
| `test_email.py` | Sends a sample email to verify credentials work |
| `config.json` | List of accounts/repos to watch, and email addresses |
| `state.json` | Auto-generated at runtime — tracks seen events between runs (not committed) |
| `.github/workflows/monitor.yml` | Runs `monitor.py` every 5 minutes via GitHub Actions |
| `.github/workflows/test_email.yml` | Runs `test_email.py` on demand |
