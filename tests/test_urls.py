"""Tests for URL enumeration (sitemap + products.json)."""

from pathlib import Path
from unittest.mock import MagicMock

from based_inventory.crawl.urls import UrlEnumerator

FIXTURES = Path(__file__).parent / "fixtures"


def _response(text="", json_data=None):
    r = MagicMock()
    r.text = text
    r.raise_for_status = MagicMock()
    if json_data is not None:
        r.json.return_value = json_data
    return r


def test_pdp_urls_from_products_json(monkeypatch):
    import json as _json

    products_json = (FIXTURES / "products_page_1.json").read_text()
    responses = [
        _response(json_data=_json.loads(products_json)),
        _response(json_data={"products": []}),
    ]
    mock_get = MagicMock(side_effect=responses)
    monkeypatch.setattr("based_inventory.crawl.urls.requests.get", mock_get)

    enumerator = UrlEnumerator(store_url="https://basedbodyworks.com")
    urls = enumerator.pdp_urls()

    assert "https://basedbodyworks.com/products/shampoo" in urls
    assert "https://basedbodyworks.com/products/curl-cream" in urls
    assert len(urls) == 2


def test_landing_pages_from_sitemap(monkeypatch):
    sitemap_index = (FIXTURES / "sitemap_index.xml").read_text()
    sitemap_pages = (FIXTURES / "sitemap_pages.xml").read_text()

    def get(url, **_kw):
        if url.endswith("sitemap.xml"):
            return _response(text=sitemap_index)
        if "sitemap_pages" in url:
            return _response(text=sitemap_pages)
        return _response(
            text='<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'
        )

    monkeypatch.setattr("based_inventory.crawl.urls.requests.get", MagicMock(side_effect=get))

    enumerator = UrlEnumerator(store_url="https://basedbodyworks.com")
    urls = enumerator.landing_page_urls()

    assert "https://basedbodyworks.com/pages/spring-launch" in urls
    assert "https://basedbodyworks.com/pages/curly-routine" in urls
    assert "https://basedbodyworks.com/pages/about" in urls
