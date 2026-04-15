"""Discover every URL a product can appear on.

Three sources:
1. `/products.json?page=N`: PDP URLs
2. `/sitemap.xml` then follow index then filter `/pages/*`: Instant Commerce landing pages
3. `/collections.json`: collection PLP URLs (also in sitemap but parsing JSON is safer)
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@dataclass(frozen=True)
class EnumeratedUrls:
    pdp: list[str]
    collection: list[str]
    landing: list[str]


class UrlEnumerator:
    def __init__(self, store_url: str) -> None:
        self.store_url = store_url.rstrip("/")

    def pdp_urls(self) -> list[str]:
        urls: list[str] = []
        page = 1
        while True:
            resp = requests.get(
                f"{self.store_url}/products.json",
                params={"limit": 250, "page": page},
                timeout=30,
            )
            resp.raise_for_status()
            products = resp.json().get("products", [])
            if not products:
                break
            urls.extend(f"{self.store_url}/products/{p['handle']}" for p in products)
            page += 1
        return urls

    def collection_urls(self) -> list[str]:
        urls: list[str] = []
        page = 1
        while True:
            resp = requests.get(
                f"{self.store_url}/collections.json",
                params={"limit": 250, "page": page},
                timeout=30,
            )
            resp.raise_for_status()
            collections = resp.json().get("collections", [])
            if not collections:
                break
            urls.extend(f"{self.store_url}/collections/{c['handle']}" for c in collections)
            page += 1
        return urls

    def landing_page_urls(self) -> list[str]:
        index_resp = requests.get(f"{self.store_url}/sitemap.xml", timeout=30)
        index_resp.raise_for_status()
        index_root = ET.fromstring(index_resp.text)

        sitemap_urls = [
            elem.text.strip()
            for elem in index_root.findall(f"{_SITEMAP_NS}sitemap/{_SITEMAP_NS}loc")
            if elem.text and "sitemap_pages" in elem.text
        ]

        urls: list[str] = []
        for sitemap_url in sitemap_urls:
            try:
                resp = requests.get(sitemap_url, timeout=30)
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
                for loc in root.findall(f"{_SITEMAP_NS}url/{_SITEMAP_NS}loc"):
                    if loc.text and "/pages/" in loc.text:
                        urls.append(loc.text.strip())
            except (requests.RequestException, ET.ParseError) as exc:
                logger.warning("Could not parse sitemap %s: %s", sitemap_url, exc)
        return urls

    def enumerate_all(self) -> EnumeratedUrls:
        return EnumeratedUrls(
            pdp=self.pdp_urls(),
            collection=self.collection_urls(),
            landing=self.landing_page_urls(),
        )
