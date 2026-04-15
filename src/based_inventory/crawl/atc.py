"""Playwright-driven ATC state detection.

Responsibilities:
1. Launch one chromium browser, reuse across URLs
2. For each URL: open page, wait for network idle, read ATC state
3. For PDP: iterate variant picker options, re-read ATC state per variant
4. Return structured VariantObservation list

Bot-detection mitigations: stealth User-Agent, throttled concurrency,
random jitter between page loads.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from types import TracebackType

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from based_inventory.crawl.selectors import ATC_SELECTORS

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class VariantObservation:
    url: str
    variant_label: str | None  # None if single-variant page or collection card
    present: bool
    enabled: bool
    text: str


class AtcCrawler:
    def __init__(self, headless: bool = True, throttle_ms: tuple = (500, 1500)) -> None:
        self.headless = headless
        self.throttle_ms = throttle_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> AtcCrawler:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _new_page(self) -> Page:
        if self._browser is None:
            raise RuntimeError("AtcCrawler must be used as a context manager")
        ctx = self._browser.new_context(user_agent=_USER_AGENT)
        return ctx.new_page()

    def _throttle(self) -> None:
        low, high = self.throttle_ms
        time.sleep(random.uniform(low, high) / 1000)

    def _read_state_on_current_page(self, page: Page) -> tuple:
        for selector in ATC_SELECTORS:
            locator = page.locator(selector).first
            if locator.count() > 0:
                try:
                    disabled = locator.is_disabled()
                    aria = (locator.get_attribute("aria-disabled") or "").lower()
                    klass = (locator.get_attribute("class") or "").lower()
                    text = (locator.inner_text(timeout=2000) or "").strip()
                    enabled = not (
                        disabled or aria == "true" or "sold-out" in klass or "soldout" in klass
                    )
                    return True, enabled, text
                except Exception as exc:
                    logger.debug("Selector %s hit but attribute read failed: %s", selector, exc)
                    continue
        return False, False, ""

    def audit_url(self, url: str) -> list:
        """Audit a single URL. For a PDP with variants, iterate variants."""
        page = self._new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=30_000)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", url, exc)
            return [
                VariantObservation(
                    url=url, variant_label=None, present=False, enabled=False, text=""
                )
            ]

        observations: list = []

        # Default: read state with no variant selected
        present, enabled, text = self._read_state_on_current_page(page)
        observations.append(
            VariantObservation(
                url=url, variant_label=None, present=present, enabled=enabled, text=text
            )
        )

        # Detect and iterate variant picker (Shopify pattern: radio inputs or select)
        variant_inputs = page.locator('fieldset input[type="radio"][name*="option" i]').all()
        if variant_inputs:
            for radio in variant_inputs:
                try:
                    label = radio.get_attribute("value") or radio.get_attribute("aria-label") or ""
                    radio.click()
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception as exc:
                    logger.debug("Variant click failed: %s", exc)
                    continue
                present, enabled, text = self._read_state_on_current_page(page)
                observations.append(
                    VariantObservation(
                        url=url,
                        variant_label=label.strip() or None,
                        present=present,
                        enabled=enabled,
                        text=text,
                    )
                )
        else:
            # Fallback: look for native <select> variant picker
            select_options = page.locator('select[name*="id" i] option[value]').all()
            for option in select_options:
                try:
                    label = option.inner_text(timeout=2000).strip() or (
                        option.get_attribute("value") or ""
                    )
                    value = option.get_attribute("value")
                    if not value:
                        continue
                    page.locator('select[name*="id" i]').first.select_option(value=value)
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception as exc:
                    logger.debug("Variant select failed: %s", exc)
                    continue
                present, enabled, text = self._read_state_on_current_page(page)
                observations.append(
                    VariantObservation(
                        url=url,
                        variant_label=label or None,
                        present=present,
                        enabled=enabled,
                        text=text,
                    )
                )

        self._throttle()
        page.context.close()
        return observations

    def audit_inline_html(self, html: str, url: str) -> list:
        """Test helper: set page content directly instead of navigating."""
        page = self._new_page()
        page.set_content(html, wait_until="load")
        present, enabled, text = self._read_state_on_current_page(page)
        page.context.close()
        return [
            VariantObservation(
                url=url, variant_label=None, present=present, enabled=enabled, text=text
            )
        ]
