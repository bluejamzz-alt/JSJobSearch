"""Send the digest by email over SMTP (Gmail app password works out of the box)."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate

log = logging.getLogger(__name__)


class EmailConfigError(RuntimeError):
    pass


def smtp_settings() -> dict:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", user)
    missing = [k for k, v in (("SMTP_USER", user), ("SMTP_PASSWORD", password)) if not v]
    if missing:
        raise EmailConfigError("Missing environment variables: " + ", ".join(missing))
    return {"host": host, "port": port, "user": user, "password": password, "sender": sender}


def recipients(to_env: str = "EMAIL_TO") -> list[str]:
    raw = os.environ.get(to_env, "") or os.environ.get("SMTP_USER", "")
    return [r.strip() for r in raw.replace(";", ",").split(",") if r.strip()]


def send_email(subject: str, html_body: str, text_body: str, to: list[str],
               attachments: dict[str, str] | None = None) -> None:
    cfg = smtp_settings()
    if not to:
        raise EmailConfigError("No recipients: set EMAIL_TO")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    for filename, content in (attachments or {}).items():
        msg.add_attachment(content.encode("utf-8"), maintype="text",
                           subtype="markdown" if filename.endswith(".md") else "plain", filename=filename)

    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=60) as smtp:
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
    log.info("Email sent to %s", ", ".join(to))
