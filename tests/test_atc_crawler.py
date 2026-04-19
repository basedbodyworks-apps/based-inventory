"""Tests for the Based-theme Playwright ATC crawler.

Uses Page.set_content() to avoid real network. HTML shapes mirror what the
live Basedbodyworks.com theme renders: custom <p>ADD TO CART</p> leaves
inside <a> or <form>, with a hidden <p>SOLD OUT</p> sibling that swaps in
when a variant is unavailable.
"""

import pytest

from based_inventory.crawl.atc import AtcCrawler

pytestmark = pytest.mark.playwright


def _with_crawler(html: str, url: str):
    with AtcCrawler(headless=True) as crawler:
        return crawler.audit_inline_html(html, url=url)


def test_pdp_visible_add_to_cart_is_observed():
    html = """
    <html>
      <body>
        <main>
          <a href="/products/shampoo">Shampoo</a>
          <form action="/cart/add">
            <p style="display:none">SOLD OUT</p>
            <a><p>ADD TO CART</p></a>
          </form>
        </main>
      </body>
    </html>
    """
    observations = _with_crawler(html, url="https://x.invalid/products/shampoo")
    assert len(observations) == 1
    obs = observations[0]
    assert obs.product_handle == "shampoo"
    assert obs.present is True
    assert obs.enabled is True
    assert obs.text.upper() == "ADD TO CART"


def test_pdp_sold_out_visible_is_observed_as_oos():
    html = """
    <html>
      <body>
        <a href="/products/hair-clay">Hair Clay</a>
        <form action="/cart/add">
          <p>SOLD OUT</p>
          <a style="display:none"><p>ADD TO CART</p></a>
        </form>
      </body>
    </html>
    """
    observations = _with_crawler(html, url="https://x.invalid/products/hair-clay")
    assert len(observations) == 1
    obs = observations[0]
    assert obs.product_handle == "hair-clay"
    assert obs.present is True
    assert obs.enabled is False
    assert obs.text.upper() == "SOLD OUT"


def test_no_atc_on_page_returns_empty_list():
    html = "<html><body><h1>Marketing content, no products</h1></body></html>"
    observations = _with_crawler(html, url="https://x.invalid/pages/about")
    assert observations == []


def test_collection_page_yields_one_observation_per_card():
    html = """
    <html>
      <body>
        <section>
          <div class="card">
            <a href="/products/shampoo"><img/>Shampoo</a>
            <form><a><p>ADD TO CART</p></a></form>
          </div>
          <div class="card">
            <a href="/products/conditioner"><img/>Conditioner</a>
            <form><a><p>ADD TO CART</p></a></form>
          </div>
          <div class="card">
            <a href="/products/hair-elixir"><img/>Hair Elixir</a>
            <form><p>SOLD OUT</p></form>
          </div>
        </section>
      </body>
    </html>
    """
    observations = _with_crawler(html, url="https://x.invalid/collections/all")
    handles = {o.product_handle: o for o in observations}
    assert set(handles) == {"shampoo", "conditioner", "hair-elixir"}
    assert handles["shampoo"].enabled is True
    assert handles["conditioner"].enabled is True
    assert handles["hair-elixir"].enabled is False


def test_hidden_and_visible_atcs_in_same_card_collapse_to_visible():
    """Based renders both <p>SOLD OUT</p> and <p>ADD TO CART</p> in the same card;
    only one is visible at a time. The crawler must keep the visible one."""
    html = """
    <html>
      <body>
        <div class="card">
          <a href="/products/curl-cream">Curl Cream</a>
          <form>
            <p style="display:none">SOLD OUT</p>
            <a><p>ADD TO CART</p></a>
          </form>
        </div>
      </body>
    </html>
    """
    observations = _with_crawler(html, url="https://x.invalid/collections/curls")
    # Expect one observation: the visible ADD TO CART for curl-cream.
    assert len(observations) == 1
    obs = observations[0]
    assert obs.product_handle == "curl-cream"
    assert obs.text.upper() == "ADD TO CART"
    assert obs.enabled is True
