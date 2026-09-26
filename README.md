# gh-monitor

Watches a GitHub account's public activity and emails you a plain-English summary whenever something new happens. Uses AI (Gemini) to explain what actually changed in the code — no developer knowledge required to understand the alerts.

## What it monitors

Any public activity by the accounts listed in `config.json`:

- Code pushed to a project
- Pull requests opened, reviewed, or merged
- Issues created or closed
- New releases published
- Repos starred or forked
- Wiki edits, collaborator changes, and more

## How summaries work

For code changes, the monitor fetches the actual file diffs and sends them to Google's Gemini API, which generates a detailed, jargon-free explanation of what was added or changed. No information is lost — the summary covers everything meaningful in each change.

If the AI is unavailable (rate limit, missing API key), it falls back to rule-based descriptions.

## What the email looks like

```
You have 2 new update(s) from octocat on GitHub.
Sent: 26 Sep 2026, 6:30 PM IST

─────────────────────────────────────────────────────────────────
[1 of 2]  Code Update  ·  octocat/hello-world  ·  26 Sep 2026, 6:28 PM IST

@octocat pushed 2 new change(s) to the "main" branch of "hello-world".

  • "Add dark mode support to homepage"
  • "Fix CSS alignment on mobile"

  What this means:  A new article titled "Dark Mode Support" has been added
                    to the website. It introduces a toggle that switches the
                    colour scheme from light to dark ...
  View on GitHub:   https://github.com/octocat/hello-world/compare/a1b2c3d4e5f6...abc1234def56

─────────────────────────────────────────────────────────────────
[2 of 2]  Pull Request  ·  octocat/tools  ·  26 Sep 2026, 6:30 PM IST

@octocat opened a pull request in "tools".

  Title: "Fix pagination bug on search results"  (PR #42)

  What this means:  A pull request is a proposal to add changes.
                    It is under review and not live yet.
  View on GitHub:   https://github.com/octocat/tools/pull/42
```

Every event includes:
- **What happened** — in plain English
- **What it means** — an AI-generated summary of the actual changes (or a fallback explanation for non-developers)
- **A direct link** to view it on GitHub

## How it works

A GitHub Actions workflow runs every 5 minutes, calls the GitHub API, and emails you if anything new is found. It remembers what it has already reported so you never get duplicate alerts. If two bursts of activity happen within 10 minutes, the second email is labelled "Update" and tells you how long since the last alert.

## Setup

### 1. Fork or clone this repo into your own GitHub account

### 2. Edit `config.json` *(optional)*

You can configure targets and email addresses in `config.json`:

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

Alternatively, you can set everything via secrets (see step 3) to keep your config private. Secrets take priority over `config.json`.

> Only **public** repositories and **public** user activity can be monitored. Private repos will return a 404 and be skipped.

### 3. Add secrets

Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|---|---|
| `MONITOR_TARGETS` | Comma-separated GitHub usernames or repos to watch (e.g. `octocat,octocat/hello-world`) |
| `EMAIL_FROM` | Gmail address to send alerts from |
| `EMAIL_TO` | Email address to receive alerts |
| `EMAIL_PASSWORD` | Gmail App Password (not your real password — see below) |
| `GEMINI_API_KEY` | Google Gemini API key ([get one free](https://aistudio.google.com/apikey)) |
| `GH_TOKEN` | *(Optional)* GitHub personal access token — increases API rate limits |

**Getting a Gmail App Password:**
1. Go to [myaccount.google.com](https://myaccount.google.com) → Security → 2-Step Verification
2. Scroll to **App passwords** and create one (name it anything, e.g. "gh-monitor")
3. Copy the 16-character password shown and paste it as the `EMAIL_PASSWORD` secret

### 4. Activate the workflow

GitHub doesn't auto-run scheduled workflows on new repos. Go to **Actions → GitHub Monitor → Run workflow** once to kick it off. After that it runs automatically every 5 minutes.

### 5. Send a test email

Go to **Actions → Test Email → Run workflow** to verify your email credentials work.

To test the full pipeline (fetch events + AI summary + email), go to **Actions → Test Summary (End-to-End) → Run workflow**. You can specify how many recent events to summarise, or test a specific commit by providing an `owner/repo` and SHA.

## Files

| File | Purpose |
|---|---|
| `monitor.py` | Main script — fetches events, gets AI summaries via Gemini, formats and sends email |
| `test_summary.py` | End-to-end test — fetches recent events or a specific commit, summarises with AI, sends email |
| `test_email.py` | Sends a sample email to verify SMTP credentials work |
| `config.json` | List of accounts/repos to watch, and email addresses |
| `state.json` | Auto-generated at runtime — tracks seen events between runs (not committed) |
| `.github/workflows/monitor.yml` | Runs `monitor.py` every 5 minutes via GitHub Actions |
| `.github/workflows/test_summary.yml` | Runs `test_summary.py` on demand |
| `.github/workflows/test_email.yml` | Runs `test_email.py` on demand |
