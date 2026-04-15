"""Telegram Bot API fallback for Slack outages.

Unlike Inventory Brain (which uses the `openclaw` CLI), this calls the
Telegram Bot API directly. No external CLI dependency.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


class TelegramFallback:
    def __init__(self, bot_token: str | None, chat_id: str | None) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.configured:
            logger.info("Telegram fallback not configured; skipping")
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text},
                timeout=15,
            )
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("Telegram send failed: %s", type(exc).__name__)
            return False

        if not result.get("ok"):
            logger.error("Telegram API error: %s", result.get("description"))
            return False

        return True
