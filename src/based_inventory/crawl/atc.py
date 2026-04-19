"""Playwright-driven ATC state detection for Based's theme.

Collects every visible ATC element on a page, tags each with the product
handle of the nearest ancestor "card" (smallest subtree containing exactly
one unique /products/{handle} link). Works for PDPs (page-wide card),
collections (one observation per product card), and landing pages
(one observation per embedded product block).

Scope notes:
- Does not currently iterate PDP variant pickers; Based uses custom
  <a>/<div> cards rather than <input type="radio">, and the default
  state is what most users see. Variant iteration is deferred to a
  follow-up once we pick a theme-specific selector for pack cards.
- Bot-detection mitigations: real desktop User-Agent, throttled
  concurrency, random jitter between page loads.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from types import TracebackType

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# JavaScript that walks the DOM and returns one entry per distinct ATC observation.
# An "ATC" is any element whose direct text content matches the ATC lexicon.
# We ignore descendant-combined text so that an <a> wrapping multiple spans doesn't
# double-count; only the leaf node matters. The ancestor walk finds the card
# boundary by looking for the smallest subtree with exactly one unique product handle.
_ATC_SCAN_JS = r"""
() => {
  const ATC_TEXT = /^(ADD TO CART|ADD TO BAG|BUY NOW|SOLD OUT|NOTIFY ME|COMING SOON|PRE[- ]?ORDER)$/i;
  const OOS_TEXT = /^(SOLD OUT|NOTIFY ME|COMING SOON|UNAVAILABLE)$/i;

  function productHandleFromCard(leafEl) {
    let cur = leafEl.parentElement;
    for (let i = 0; i < 20 && cur; i++) {
      const links = cur.querySelectorAll('a[href*="/products/"]');
      const handles = new Set();
      for (const a of links) {
        const href = (a.getAttribute('href') || '').split('?')[0];
        const m = href.match(/\/products\/([^/]+)/);
        if (m) handles.add(m[1]);
      }
      if (handles.size === 1) return [...handles][0];
      cur = cur.parentElement;
    }
    return null;
  }

  // PDP fallback: if the page URL is /products/{handle}, every ATC on the page
  // that doesn't resolve to a different card belongs to this product.
  const pageHandle = (() => {
    const m = (location.pathname || '').match(/\/products\/([^/]+)/);
    return m ? m[1] : null;
  })();

  // Collect only VISIBLE ATC leaves. Based's theme renders a hidden
  // <p>SOLD OUT</p> sibling next to the visible <p>ADD TO CART</p>
  // (or vice-versa) so the UI can swap state without re-rendering.
  // The hidden one is not a real observation — it's shadow state waiting
  // to be revealed. Only the visible one represents what customers see.
  const out = [];
  document.querySelectorAll('*').forEach(el => {
    if (!(el.childNodes && el.childNodes.length === 1 && el.childNodes[0].nodeType === 3)) return;
    const text = (el.textContent || '').trim();
    if (!ATC_TEXT.test(text)) return;
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0;
    if (!visible) return;
    const handle = productHandleFromCard(el) || pageHandle;
    const isOos = OOS_TEXT.test(text);
    out.push({
      product_handle: handle,
      text,
      visible: true,
      enabled: !isOos,
      top_y: Math.round(rect.top + window.scrollY),
    });
  });

  // Dedupe by (product_handle, top_y) in case a card renders the same
  // visible ATC twice (e.g. desktop + mobile variants with the same layout).
  const byKey = new Map();
  for (const obs of out) {
    const k = `${obs.product_handle}::${obs.top_y}`;
    if (!byKey.has(k)) byKey.set(k, obs);
  }
  return [...byKey.values()];
}
"""


@dataclass(frozen=True)
class VariantObservation:
    url: str
    product_handle: str | None
    variant_label: str | None
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

    def audit_url(self, url: str) -> list[VariantObservation]:
        """Audit a single URL. Returns one VariantObservation per ATC element on the page."""
        page = self._new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=30_000)
            # Some Based pages hydrate product cards lazily on scroll; nudge them.
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", url, exc)
            page.context.close()
            return []

        try:
            raw = page.evaluate(_ATC_SCAN_JS)
        except Exception as exc:
            logger.warning("ATC scan failed on %s: %s", url, exc)
            page.context.close()
            return []

        observations = [
            VariantObservation(
                url=url,
                product_handle=entry.get("product_handle"),
                variant_label=None,
                present=bool(entry.get("visible")),
                enabled=bool(entry.get("enabled")),
                text=entry.get("text", ""),
            )
            for entry in (raw or [])
        ]

        self._throttle()
        page.context.close()
        return observations

    def audit_inline_html(self, html: str, url: str) -> list[VariantObservation]:
        """Test helper: set page content directly instead of navigating."""
        page = self._new_page()
        page.set_content(html, wait_until="load")
        try:
            raw = page.evaluate(_ATC_SCAN_JS)
        finally:
            page.context.close()
        return [
            VariantObservation(
                url=url,
                product_handle=entry.get("product_handle"),
                variant_label=None,
                present=bool(entry.get("visible")),
                enabled=bool(entry.get("enabled")),
                text=entry.get("text", ""),
            )
            for entry in (raw or [])
        ]
