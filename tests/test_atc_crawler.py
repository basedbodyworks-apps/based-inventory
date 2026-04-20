"""Tests for the Based-theme Playwright ATC crawler."""

import pytest

from based_inventory.crawl.atc import AtcCrawler

pytestmark = pytest.mark.playwright


def _with_crawler(html: str, url: str, variant_labels=None):
    with AtcCrawler(headless=True) as crawler:
        return crawler.audit_inline_html(html, url=url, variant_labels=variant_labels)


def test_pdp_visible_add_to_cart_is_observed():
    html = """
    <html><body>
      <main>
        <a href="/products/shampoo">Shampoo</a>
        <form action="/cart/add">
          <p style="display:none">SOLD OUT</p>
          <a><p>ADD TO CART</p></a>
        </form>
      </main>
    </body></html>
    """
    observations = _with_crawler(html, url="https://x.invalid/products/shampoo")
    assert len(observations) == 1
    obs = observations[0]
    assert obs.product_handle == "shampoo"
    assert obs.variant_label is None  # default-state (no variant_labels passed)
    assert obs.present is True
    assert obs.enabled is True
    assert obs.text.upper() == "ADD TO CART"


def test_pdp_sold_out_visible_is_observed_as_oos():
    html = """
    <html><body>
      <a href="/products/hair-clay">Hair Clay</a>
      <form action="/cart/add">
        <p>SOLD OUT</p>
        <a style="display:none"><p>ADD TO CART</p></a>
      </form>
    </body></html>
    """
    observations = _with_crawler(html, url="https://x.invalid/products/hair-clay")
    assert len(observations) == 1
    obs = observations[0]
    assert obs.product_handle == "hair-clay"
    assert obs.enabled is False
    assert obs.text.upper() == "SOLD OUT"


def test_no_atc_on_page_returns_empty_list():
    html = "<html><body><h1>Marketing content, no products</h1></body></html>"
    observations = _with_crawler(html, url="https://x.invalid/pages/about")
    assert observations == []


def test_collection_page_yields_one_observation_per_card():
    html = """
    <html><body>
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
    </body></html>
    """
    observations = _with_crawler(html, url="https://x.invalid/collections/all")
    handles = {o.product_handle: o for o in observations}
    assert set(handles) == {"shampoo", "conditioner", "hair-elixir"}
    assert handles["shampoo"].enabled is True
    assert handles["conditioner"].enabled is True
    assert handles["hair-elixir"].enabled is False


def test_pdp_variant_iteration_clicks_picker_and_emits_per_variant():
    """Passing variant_labels triggers picker iteration and one observation per label."""
    # Minimal JS-powered variant picker: clicking a <p> label toggles the
    # visible ATC text via a sibling dataset trick — mirrors Based's pattern
    # where clicking scent buttons swaps which <p> is visible.
    html = """
    <html><body>
      <a href="/products/body-care-set">Body Care Set</a>
      <form action="/cart/add" id="form">
        <p id="atc_santal_santal" style="display:none">ADD TO CART</p>
        <p id="atc_guava_guava" style="display:none">SOLD OUT</p>
        <p id="atc_default">SOLD OUT</p>
      </form>
      <div id="picker">
        <p data-variant="Santal Sandalwood + Santal Sandalwood"
           onclick="document.getElementById('atc_default').style.display='none';
                    document.getElementById('atc_santal_santal').style.display='';
                    document.getElementById('atc_guava_guava').style.display='none';">
          Santal Sandalwood + Santal Sandalwood
        </p>
        <p data-variant="Guava Nectar + Guava Nectar"
           onclick="document.getElementById('atc_default').style.display='none';
                    document.getElementById('atc_santal_santal').style.display='none';
                    document.getElementById('atc_guava_guava').style.display='';">
          Guava Nectar + Guava Nectar
        </p>
      </div>
    </body></html>
    """
    observations = _with_crawler(
        html,
        url="https://x.invalid/products/body-care-set",
        variant_labels=[
            "Santal Sandalwood + Santal Sandalwood",
            "Guava Nectar + Guava Nectar",
        ],
    )
    by_label = {o.variant_label: o for o in observations}
    assert set(by_label) == {
        "Santal Sandalwood + Santal Sandalwood",
        "Guava Nectar + Guava Nectar",
    }
    santal = by_label["Santal Sandalwood + Santal Sandalwood"]
    guava = by_label["Guava Nectar + Guava Nectar"]
    assert santal.product_handle == "body-care-set"
    assert santal.text.upper() == "ADD TO CART"
    assert santal.enabled is True
    assert guava.product_handle == "body-care-set"
    assert guava.text.upper() == "SOLD OUT"
    assert guava.enabled is False


def test_pdp_variant_iteration_falls_back_to_default_when_no_labels_match():
    """If the caller supplies labels but none exist on the page, emit the
    default-state observation so the audit still has a signal."""
    html = """
    <html><body>
      <a href="/products/mystery">Mystery</a>
      <form action="/cart/add">
        <p>ADD TO CART</p>
      </form>
    </body></html>
    """
    observations = _with_crawler(
        html,
        url="https://x.invalid/products/mystery",
        variant_labels=["Santal Sandalwood", "Guava Nectar"],  # no match on this HTML
    )
    assert len(observations) == 1
    assert observations[0].product_handle == "mystery"
    assert observations[0].variant_label is None
    assert observations[0].text.upper() == "ADD TO CART"
