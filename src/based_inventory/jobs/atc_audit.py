"""Daily 6am PST: crawl every page on the site, diff ATC vs Shopify truth.

Flow:
1. Pull Shopify products with inventory + inventoryPolicy
2. Enumerate every sitemap URL (PDPs, collections, pages, blog, homepage)
3. Compute expected sellable per product using singles-only math (product-level,
   since v0.5 observes default variant only; per-variant iteration is v0.6)
4. Playwright-render each URL, observe every visible ATC + the product
   handle of the card containing it
5. Diff each observation against the matching product's expected state
6. Dedup against persistent state; post only NEW flags
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from based_inventory.auth import fetch_access_token
from based_inventory.config import Config
from based_inventory.crawl.atc import AtcCrawler, VariantObservation
from based_inventory.crawl.diff import ExpectedState, Flag, FlagType, generate_flags
from based_inventory.crawl.urls import UrlEnumerator
from based_inventory.jobs._common import run_job
from based_inventory.sets import SetResolver
from based_inventory.shopify import ShopifyClient
from based_inventory.singles import resolve_single
from based_inventory.skip_list import should_skip
from based_inventory.slack import SlackClient, context, divider, header, section
from based_inventory.state import AlertState

logger = logging.getLogger(__name__)

FLAG_ICONS = {
    FlagType.SALES_LEAK: "🛒",
    FlagType.OVERSELL_RISK: "⚠️",
    FlagType.NO_BUY_BUTTON: "👻",
}

FLAG_LABELS = {
    FlagType.SALES_LEAK: "SALES LEAK",
    FlagType.OVERSELL_RISK: "OVERSELL RISK",
    FlagType.NO_BUY_BUTTON: "NO BUY BUTTON",
}

V0_LIMITATION_FOOTER = (
    "⚠️ v0 limitation: trusts Shopify as source of truth. "
    "Shopify and ShipHero can drift. Verify against ShipHero before action."
)

COMPONENTS_PATH = Path(__file__).resolve().parents[3] / "data" / "set-components.json"


@dataclass(frozen=True)
class ExpectedProduct:
    """Expected state aggregated at the product-handle level.

    V0.5 audits default variants only (the one rendered on collection cards
    and the default pick on a PDP), so we collapse variant-level math into
    a single sellable flag per product. Per-variant observation is v0.6.
    """

    product_handle: str
    product_title: str
    variant_gid: str  # representative variant (the "single" or default) for dedup keys
    expected: ExpectedState


def _sellable_from_levels(variant: dict[str, Any]) -> bool:
    levels = variant.get("inventoryItem", {}).get("inventoryLevels", [])
    return any(
        lvl["available"] > 0 and lvl["location"].get("shipsInventory", True) for lvl in levels
    )


def _pick_default_variant(product: dict[str, Any]) -> dict[str, Any]:
    """Pick the variant most likely to render as the default on the storefront.

    Heuristic: prefer one whose title matches the singles/default patterns;
    otherwise fall back to the first variant.
    """
    for v in product.get("variants", []):
        title = (v.get("title") or "").lower()
        if any(x in title for x in ("just one", "just ", "single")) or title in (
            "default title",
            "full size",
        ):
            return v
    return product["variants"][0]


def compute_expected_products(
    products: list[dict[str, Any]],
    set_resolver: SetResolver,
) -> dict[str, ExpectedProduct]:
    """Return product_handle -> ExpectedProduct for every auditable product."""
    expected_by_handle: dict[str, ExpectedProduct] = {}
    singles_by_title = {p["title"]: resolve_single(p).qty for p in products}

    for product in products:
        title = product["title"]
        handle = product.get("handle")
        if not handle:
            continue
        if should_skip(title):
            continue
        if title in ("Daily Facial Cleanser", "Daily Facial Moisturizer"):
            # No public PDP for these; they live as variants of Daily Skincare Duo.
            continue

        default_variant = _pick_default_variant(product)
        policy = default_variant.get("inventoryPolicy", "DENY")

        if set_resolver.is_set(title):
            capacity = set_resolver.capacity(title, singles_by_title)
            sellable = capacity > 0
        else:
            # Product-level sellable: singles math on the default variant
            sellable = _sellable_from_levels(default_variant)

        expected_by_handle[handle] = ExpectedProduct(
            product_handle=handle,
            product_title=title,
            variant_gid=default_variant["id"],
            expected=ExpectedState(sellable=sellable, inventory_policy=policy),
        )

    return expected_by_handle


def _dedupe_flags_by_state_key(flags: list[Flag]) -> list[Flag]:
    """Keep the first occurrence of each state_key."""
    seen: set[str] = set()
    out: list[Flag] = []
    for flag in flags:
        if flag.state_key in seen:
            continue
        seen.add(flag.state_key)
        out.append(flag)
    return out


def build_atc_blocks(flags: list[Flag]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        header(f"⚡ ATC Audit: {len(flags)} disagreement(s)"),
        divider(),
    ]

    order = {FlagType.OVERSELL_RISK: 0, FlagType.SALES_LEAK: 1, FlagType.NO_BUY_BUTTON: 2}
    sorted_flags = sorted(flags, key=lambda f: (order[f.flag_type], f.product_title))

    for f in sorted_flags:
        icon = FLAG_ICONS[f.flag_type]
        label = FLAG_LABELS[f.flag_type]
        variant_part = f" / {f.variant_label}" if f.variant_label else ""
        body = (
            f"{icon} {label} - *{f.product_title}*{variant_part}\n"
            f"🔗 <{f.url}|{f.url}>\n"
            f'💬 Observed: "{f.observed_text}"'
        )
        if f.flag_type == FlagType.OVERSELL_RISK:
            body += f"\n{V0_LIMITATION_FOOTER}"
        blocks.append(section(body))

    blocks.append(divider())

    ts = time.strftime("%b %d, %I:%M %p PST", time.gmtime(time.time() - 7 * 3600))
    blocks.append(context(f"🕐 {ts}"))
    return blocks


def _pdp_handle_from_url(url: str) -> str | None:
    """Return product handle if URL is /products/{handle}, else None."""
    marker = "/products/"
    if marker not in url:
        return None
    tail = url.split(marker, 1)[1]
    return tail.split("/")[0].split("?")[0] or None


def _flags_for_observation(
    obs: VariantObservation,
    expected_by_handle: dict[str, ExpectedProduct],
) -> list[Flag]:
    """Emit flags for a single (observation) against the product it came from."""
    if not obs.product_handle:
        # Can't attribute this ATC to a product; skip rather than mis-route.
        return []

    product = expected_by_handle.get(obs.product_handle)
    if product is None:
        # ATC for a product that's skipped or not in the active catalog; ignore.
        return []

    return generate_flags(
        expected=product.expected,
        observed=obs,
        variant_gid=product.variant_gid,
        product_title=product.product_title,
    )


def _run(cfg: Config) -> None:
    token = fetch_access_token(cfg.shopify_store, cfg.shopify_client_id, cfg.shopify_client_secret)
    shopify = ShopifyClient(cfg.shopify_store, token, cfg.shopify_api_version)
    set_resolver = SetResolver(COMPONENTS_PATH)
    state = AlertState.load(cfg.state_path)
    slack = SlackClient(cfg.slack_bot_token, cfg.slack_channel, dry_run=cfg.dry_run)
    url_enum = UrlEnumerator(f"https://{cfg.shopify_store.replace('.myshopify.com', '.com')}")

    logger.info("Fetching Shopify products")
    products = shopify.fetch_all_products()
    expected_by_handle = compute_expected_products(products, set_resolver)

    logger.info("Enumerating URLs")
    urls = url_enum.enumerate_all()
    logger.info(
        "Auditing %d URLs (pdp=%d, collection=%d, landing=%d, other=%d)",
        len(urls.all_urls),
        len(urls.pdp),
        len(urls.collection),
        len(urls.landing),
        len(urls.other),
    )

    pdp_handles_in_sitemap = {_pdp_handle_from_url(u) for u in urls.pdp}
    pdp_handles_in_sitemap.discard(None)

    all_flags: list[Flag] = []
    with AtcCrawler(headless=True) as crawler:
        for url in urls.all_urls:
            try:
                observations = crawler.audit_url(url)
            except Exception as exc:
                logger.warning("Crawl failed for %s: %s", url, exc)
                continue

            observed_handles_here: set[str] = set()
            for obs in observations:
                all_flags.extend(_flags_for_observation(obs, expected_by_handle))
                if obs.product_handle:
                    observed_handles_here.add(obs.product_handle)

            # PDP URL where the page's own product produced no observation:
            # the theme is rendering without any visible ATC for it.
            page_handle = _pdp_handle_from_url(url)
            if (
                page_handle
                and page_handle in expected_by_handle
                and page_handle not in observed_handles_here
            ):
                product = expected_by_handle[page_handle]
                all_flags.append(
                    Flag(
                        flag_type=FlagType.NO_BUY_BUTTON,
                        product_title=product.product_title,
                        variant_gid=product.variant_gid,
                        variant_label=None,
                        url=url,
                        expected_sellable=product.expected.sellable,
                        observed_text="",
                        state_key=f"{product.variant_gid}::{url}::NO_BUY_BUTTON",
                    )
                )

    logger.info("Found %d flags", len(all_flags))

    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    new_flags = _dedupe_flags_by_state_key(
        [f for f in all_flags if state.is_new_atc_flag(f.state_key)]
    )

    state.retain_only_atc_flags({f.state_key for f in all_flags})
    for f in all_flags:
        state.mark_atc_flag(f.state_key, now=now_iso)
    state.save(cfg.state_path)

    if not new_flags:
        logger.info("No new flags to post")
        return

    blocks = build_atc_blocks(new_flags)
    fallback = f"⚡ ATC Audit: {len(new_flags)} new disagreement(s)"
    slack.post_message(fallback, blocks)


def main() -> None:
    run_job("atc_audit", _run)


if __name__ == "__main__":
    main()
