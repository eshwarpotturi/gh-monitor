#!/usr/bin/env python3
"""One-shot test — sends a sample alert email using the same credentials as monitor.py."""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

email_from    = os.environ.get("EMAIL_FROM", "")
email_to      = os.environ.get("EMAIL_TO", "")
email_password= os.environ.get("EMAIL_PASSWORD", "")
smtp_server   = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
smtp_port     = int(os.environ.get("SMTP_PORT", "465"))

subject = "[GitHub Alert] Test — sai.eshwar2000@gmail.com"
body = """\
GitHub Alert  |  Test Run
Monitoring : sanand0
Showing    : 1 new event(s)

=================================================================

[2026-09-26 13:00:00 UTC]  sanand0/some-repo
PUSH by @sanand0 → main
    • example: add new feature  [abc1234]
  → https://github.com/sanand0/some-repo/commit/abc1234

=================================================================

This is a test email. If you received this, alerts are working correctly.
"""

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"]    = email_from
msg["To"]      = email_to
msg.attach(MIMEText(body, "plain"))

print(f"Sending test email from {email_from} to {email_to} ...")
with smtplib.SMTP_SSL(smtp_server, smtp_port) as s:
    s.login(email_from, email_password)
    s.sendmail(email_from, email_to, msg.as_string())
print("Done. Check your inbox.")
