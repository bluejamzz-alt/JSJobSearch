"""Optional Telegram push (bot token + chat id from environment)."""

from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("Telegram enabled but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping")
        return False
    if not text:
        return False
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
        timeout=30,
    )
    if resp.status_code != 200:
        log.error("Telegram send failed: HTTP %s %s", resp.status_code, resp.text[:200])
        return False
    return True
