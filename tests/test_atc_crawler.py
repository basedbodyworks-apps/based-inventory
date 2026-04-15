"""Tests for Playwright-driven ATC audit.

Uses Page.set_content() to avoid real network. Requires playwright install
during test setup (already in requirements-dev.txt).
"""

import pytest

from based_inventory.crawl.atc import AtcCrawler, VariantObservation  # noqa: F401

pytestmark = pytest.mark.playwright


def test_static_page_with_in_stock_atc():
    """Single-variant product page with a present+enabled ATC button."""
    html = """
    <html><body>
      <form id="product-form-1" action="/cart/add">
        <button name="add" type="submit">Add to cart</button>
      </form>
    </body></html>
    """
    with AtcCrawler(headless=True) as crawler:
        observations = crawler.audit_inline_html(html, url="https://test.invalid/products/x")
    assert len(observations) == 1
    obs = observations[0]
    assert obs.present is True
    assert obs.enabled is True
    assert "add to cart" in obs.text.lower()


def test_sold_out_page():
    html = """
    <html><body>
      <form id="product-form-1" action="/cart/add">
        <button name="add" type="submit" disabled aria-disabled="true">Sold out</button>
      </form>
    </body></html>
    """
    with AtcCrawler(headless=True) as crawler:
        observations = crawler.audit_inline_html(html, url="https://test.invalid/products/y")
    obs = observations[0]
    assert obs.present is True
    assert obs.enabled is False
    assert "sold out" in obs.text.lower()


def test_no_atc_element_page():
    html = "<html><body><h1>Nothing here</h1></body></html>"
    with AtcCrawler(headless=True) as crawler:
        observations = crawler.audit_inline_html(html, url="https://test.invalid/pages/a")
    obs = observations[0]
    assert obs.present is False
