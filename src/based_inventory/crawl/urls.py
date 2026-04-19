"""Discover every public URL on the storefront.

Follows sitemap.xml (a sitemap index) through each child sitemap and collects
every <loc> it finds. This covers PDPs, collection pages, /pages/* landing
pages, blog posts, and the homepage. The ATC crawler then visits each URL
and records observations per product card it finds there.

URL classification (PDP / collection / landing) is still surfaced for callers
that want to route behavior, but `all_urls` is the canonical list the audit
iterates. Deduped, Shopify-internal URLs (/cart, /account, etc.) excluded.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# Path prefixes we intentionally skip: Shopify-managed flows that have no
# inventory-bearing ATC and that a bot probably shouldn't hit repeatedly.
_SKIP_PATH_PREFIXES = (
    "/cart",
    "/account",
    "/checkout",
    "/apps/",
    "/tools/",
    "/search",
    "/policies/",
    "/44",  # Shopify-reserved numeric paths occasionally appear in sitemap
)


@dataclass(frozen=True)
class EnumeratedUrls:
    all_urls: list[str]
    pdp: list[str] = field(default_factory=list)
    collection: list[str] = field(default_factory=list)
    landing: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)


def _should_skip(url: str) -> bool:
    path = urlparse(url).path or "/"
    return any(path.startswith(p) for p in _SKIP_PATH_PREFIXES)


def _classify(url: str) -> str:
    path = urlparse(url).path or "/"
    if path.startswith("/products/"):
        return "pdp"
    if path.startswith("/collections/"):
        return "collection"
    if path.startswith("/pages/"):
        return "landing"
    return "other"


class UrlEnumerator:
    def __init__(self, store_url: str, timeout: int = 30) -> None:
        self.store_url = store_url.rstrip("/")
        self.timeout = timeout

    def _fetch_sitemap(self, sitemap_url: str) -> ET.Element | None:
        try:
            resp = requests.get(sitemap_url, timeout=self.timeout)
            resp.raise_for_status()
            return ET.fromstring(resp.text)
        except (requests.RequestException, ET.ParseError) as exc:
            logger.warning("Could not fetch sitemap %s: %s", sitemap_url, exc)
            return None

    def _walk_sitemap_tree(self, root_url: str) -> list[str]:
        """Walk a sitemap index recursively; return every <loc> URL."""
        to_visit = [root_url]
        seen_sitemaps: set[str] = set()
        urls: list[str] = []

        while to_visit:
            current = to_visit.pop()
            if current in seen_sitemaps:
                continue
            seen_sitemaps.add(current)

            root = self._fetch_sitemap(current)
            if root is None:
                continue

            child_sitemaps = [
                elem.text.strip()
                for elem in root.findall(f"{_SITEMAP_NS}sitemap/{_SITEMAP_NS}loc")
                if elem.text
            ]
            to_visit.extend(child_sitemaps)

            for loc in root.findall(f"{_SITEMAP_NS}url/{_SITEMAP_NS}loc"):
                if loc.text:
                    urls.append(loc.text.strip())

        return urls

    def enumerate_all(self) -> EnumeratedUrls:
        """Return every public URL advertised by sitemap.xml plus the homepage."""
        sitemap_root = f"{self.store_url}/sitemap.xml"
        raw = self._walk_sitemap_tree(sitemap_root)

        # Always include the homepage even if sitemap omits it
        raw.append(self.store_url + "/")

        # Dedupe, strip fragments/queries, drop skip-list paths
        seen: set[str] = set()
        ordered: list[str] = []
        for url in raw:
            normalized = url.split("#")[0].split("?")[0]
            if normalized in seen:
                continue
            if _should_skip(normalized):
                continue
            seen.add(normalized)
            ordered.append(normalized)

        pdp: list[str] = []
        collection: list[str] = []
        landing: list[str] = []
        other: list[str] = []
        for url in ordered:
            kind = _classify(url)
            if kind == "pdp":
                pdp.append(url)
            elif kind == "collection":
                collection.append(url)
            elif kind == "landing":
                landing.append(url)
            else:
                other.append(url)

        return EnumeratedUrls(
            all_urls=ordered,
            pdp=pdp,
            collection=collection,
            landing=landing,
            other=other,
        )
