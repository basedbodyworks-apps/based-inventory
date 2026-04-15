"""Slack Block Kit client for #alerts-inventory posts."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

_POST_URL = "https://slack.com/api/chat.postMessage"


class SlackClient:
    def __init__(self, token: str, channel: str, dry_run: bool = False) -> None:
        self.token = token
        self.channel = channel
        self.dry_run = dry_run

    def post_message(self, fallback_text: str, blocks: list[dict[str, Any]]) -> bool:
        payload = {
            "channel": self.channel,
            "text": fallback_text,
            "blocks": blocks,
            "unfurl_links": False,
        }

        if self.dry_run:
            print("[DRY_RUN] Slack post:")
            print(f"  channel: {self.channel}")
            print(f"  text: {fallback_text}")
            print(f"  blocks: {json.dumps(blocks, indent=2)}")
            return True

        try:
            response = requests.post(
                _POST_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            result = response.json()
        except requests.RequestException as exc:
            logger.error("Slack post failed: %s", exc)
            return False

        if not result.get("ok"):
            logger.error("Slack API error: %s", result.get("error", "unknown"))
            return False

        return True


def section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def divider() -> dict[str, Any]:
    return {"type": "divider"}


def header(text: str) -> dict[str, Any]:
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}
