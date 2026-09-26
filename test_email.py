#!/usr/bin/env python3
"""
Sends a sample alert email using the same credentials as monitor.py.
Run via the "Test Email" workflow in GitHub Actions, or locally if you
export EMAIL_FROM, EMAIL_TO, and EMAIL_PASSWORD as environment variables.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

email_from     = os.environ.get("EMAIL_FROM", "")
email_to       = os.environ.get("EMAIL_TO",   "")
email_password = os.environ.get("EMAIL_PASSWORD", "")
smtp_server    = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
smtp_port      = int(os.environ.get("SMTP_PORT", "465"))

subject = "[GitHub Alert] New activity from octocat  (TEST)"

body = """\
You have 2 new update(s) from octocat on GitHub.
Sent: 26 Sep 2026, 1:00 PM UTC

─────────────────────────────────────────────────────────────────
[1 of 2]  Code Update  ·  octocat/hello-world  ·  26 Sep 2026, 12:58 PM UTC

@octocat pushed 2 new change(s) to the "main" branch of "hello-world".

  • "Add dark mode support to homepage"
  • "Fix CSS alignment on mobile"

  What this means:  New code was added or modified in this project — could be a bug fix, new feature, or other improvement.
  View on GitHub:   https://github.com/octocat/hello-world/commit/abc1234

─────────────────────────────────────────────────────────────────
[2 of 2]  Pull Request  ·  octocat/tools  ·  26 Sep 2026, 1:00 PM UTC

@octocat opened a pull request in "tools".

  Title: "Fix pagination bug on search results"  (PR #42)

  What this means:  A pull request is a proposal to add changes. It is under review and not live yet.
  View on GitHub:   https://github.com/octocat/tools/pull/42

─────────────────────────────────────────────────────────────────
Monitoring: octocat  |  Sent by gh-monitor

This is a TEST email. If you received this, alerts are working correctly.
"""

print(f"Sending test email from {email_from} to {email_to} ...")
msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"]    = email_from
msg["To"]      = email_to
msg.attach(MIMEText(body, "plain"))

with smtplib.SMTP_SSL(smtp_server, smtp_port) as s:
    s.login(email_from, email_password)
    s.sendmail(email_from, email_to, msg.as_string())

print("Done. Check your inbox.")
