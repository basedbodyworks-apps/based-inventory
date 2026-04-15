"""Common job infrastructure: logging, top-level exception handling, Telegram escalation."""

from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import Callable

from based_inventory.config import Config
from based_inventory.telegram import TelegramFallback

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run_job(job_name: str, func: Callable[[Config], None]) -> None:
    """Run a job with standard error handling. Logs to stdout; Telegram on fatal error."""
    cfg = Config.from_env()
    configure_logging(cfg.log_level)

    try:
        logger.info("Starting job: %s (env=%s, dry_run=%s)", job_name, cfg.env, cfg.dry_run)
        func(cfg)
        logger.info("Job complete: %s", job_name)
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Job %s FAILED: %s\n%s", job_name, exc, tb)
        tg = TelegramFallback(cfg.telegram_bot_token, cfg.telegram_chat_id)
        tg.send(f"❌ {job_name} FAILED\n\n{type(exc).__name__}: {exc}")
        sys.exit(1)
