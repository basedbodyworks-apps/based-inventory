"""Tests for sitemap-based URL enumeration."""

from unittest.mock import MagicMock

from based_inventory.crawl.urls import UrlEnumerator


def _xml(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.raise_for_status = MagicMock()
    return r


SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://basedbodyworks.com/sitemap_products_1.xml</loc></sitemap>
  <sitemap><loc>https://basedbodyworks.com/sitemap_collections_1.xml</loc></sitemap>
  <sitemap><loc>https://basedbodyworks.com/sitemap_pages_1.xml</loc></sitemap>
  <sitemap><loc>https://basedbodyworks.com/sitemap_blogs_1.xml</loc></sitemap>
</sitemapindex>
"""

SITEMAP_PRODUCTS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://basedbodyworks.com/products/shampoo</loc></url>
  <url><loc>https://basedbodyworks.com/products/curl-cream</loc></url>
</urlset>
"""

SITEMAP_COLLECTIONS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://basedbodyworks.com/collections/all</loc></url>
  <url><loc>https://basedbodyworks.com/collections/hair</loc></url>
</urlset>
"""

SITEMAP_PAGES = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://basedbodyworks.com/pages/about</loc></url>
  <url><loc>https://basedbodyworks.com/pages/routine-quiz</loc></url>
</urlset>
"""

SITEMAP_BLOGS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://basedbodyworks.com/blogs/news/launch-day</loc></url>
</urlset>
"""


def _route(sitemap_index=SITEMAP_INDEX):
    def get(url, **_kw):
        if url.endswith("/sitemap.xml"):
            return _xml(sitemap_index)
        if "sitemap_products" in url:
            return _xml(SITEMAP_PRODUCTS)
        if "sitemap_collections" in url:
            return _xml(SITEMAP_COLLECTIONS)
        if "sitemap_pages" in url:
            return _xml(SITEMAP_PAGES)
        if "sitemap_blogs" in url:
            return _xml(SITEMAP_BLOGS)
        return _xml(
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'
        )

    return MagicMock(side_effect=get)


def test_enumerate_all_returns_every_sitemap_url(monkeypatch):
    monkeypatch.setattr("based_inventory.crawl.urls.requests.get", _route())

    enum = UrlEnumerator(store_url="https://basedbodyworks.com")
    result = enum.enumerate_all()

    # Every loc ends up in all_urls, deduped; homepage included
    assert "https://basedbodyworks.com/products/shampoo" in result.all_urls
    assert "https://basedbodyworks.com/collections/all" in result.all_urls
    assert "https://basedbodyworks.com/pages/routine-quiz" in result.all_urls
    assert "https://basedbodyworks.com/blogs/news/launch-day" in result.all_urls
    assert "https://basedbodyworks.com/" in result.all_urls


def test_enumerate_all_classifies_urls(monkeypatch):
    monkeypatch.setattr("based_inventory.crawl.urls.requests.get", _route())

    enum = UrlEnumerator(store_url="https://basedbodyworks.com")
    result = enum.enumerate_all()

    assert sorted(result.pdp) == [
        "https://basedbodyworks.com/products/curl-cream",
        "https://basedbodyworks.com/products/shampoo",
    ]
    assert sorted(result.collection) == [
        "https://basedbodyworks.com/collections/all",
        "https://basedbodyworks.com/collections/hair",
    ]
    assert sorted(result.landing) == [
        "https://basedbodyworks.com/pages/about",
        "https://basedbodyworks.com/pages/routine-quiz",
    ]
    # Blog posts + homepage land in "other"
    assert "https://basedbodyworks.com/blogs/news/launch-day" in result.other
    assert "https://basedbodyworks.com/" in result.other


def test_enumerate_all_dedupes_and_skips_shopify_internals(monkeypatch):
    index_with_noise = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://basedbodyworks.com/sitemap_products_1.xml</loc></sitemap>
</sitemapindex>
"""
    products_with_duplicates_and_cart = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://basedbodyworks.com/products/shampoo</loc></url>
  <url><loc>https://basedbodyworks.com/products/shampoo?utm=x</loc></url>
  <url><loc>https://basedbodyworks.com/cart</loc></url>
  <url><loc>https://basedbodyworks.com/account/login</loc></url>
</urlset>
"""

    def get(url, **_kw):
        if url.endswith("/sitemap.xml"):
            return _xml(index_with_noise)
        if "sitemap_products" in url:
            return _xml(products_with_duplicates_and_cart)
        return _xml(
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'
        )

    monkeypatch.setattr("based_inventory.crawl.urls.requests.get", MagicMock(side_effect=get))

    enum = UrlEnumerator(store_url="https://basedbodyworks.com")
    result = enum.enumerate_all()

    # Both shampoo variants collapse to one (query stripped, deduped)
    shampoo_count = sum(1 for u in result.all_urls if u.endswith("/products/shampoo"))
    assert shampoo_count == 1
    # Shopify-internal paths skipped
    assert not any("/cart" in u for u in result.all_urls)
    assert not any("/account" in u for u in result.all_urls)
