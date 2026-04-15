"""ATC button selector list with fallbacks.

Ordered from most-specific to most-fuzzy. First match wins.
Runs against rendered DOM (Playwright) in the crawler, and against
static HTML (BeautifulSoup) in unit-test fallback.
"""

from __future__ import annotations

# CSS selectors tried in order
ATC_SELECTORS: list[str] = [
    'button[name="add"]',
    'button[type="submit"][form*="product-form"]',
    'form[action*="/cart/add"] button[type="submit"]',
    '[data-testid*="add-to-cart" i]',
    "[data-atc]",  # common Instant Commerce attribute; update after live inspection
]

# Text regex for fallback matching on button innerText
ATC_TEXT_PATTERN = r"(?i)(add to cart|sold out|notify me|coming soon)"
