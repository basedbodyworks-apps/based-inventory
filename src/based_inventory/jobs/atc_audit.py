"""Daily 6am PST: crawl site, diff ATC vs Shopify truth, alert on disagreements.

Flow:
1. Pull Shopify products with inventory + inventoryPolicy per variant
2. Enumerate URLs (PDPs + collections + /pages/* via sitemap)
3. Compute expected sellable per variant using singles-only math
4. Playwright-render each URL, observe ATC state per variant
5. Diff; flags (SALES_LEAK / OVERSELL_RISK / NO_BUY_BUTTON)
6. Dedup against alert-state.json (post only NEW flags)
7. Post Block Kit alert, prune resolved flags from state
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
class ExpectedVariant:
    """Expected state for one (product, variant) under singles-only math."""

    variant_gid: str
    product_title: str
    variant_label: str
    expected: ExpectedState


def _sellable_from_levels(variant: dict[str, Any]) -> bool:
    levels = variant.get("inventoryItem", {}).get("inventoryLevels", [])
    return any(
        lvl["available"] > 0 and lvl["location"].get("shipsInventory", True) for lvl in levels
    )


def _single_variant_qty_for_product(product: dict[str, Any]) -> int:
    """Use resolve_single to get the effective single qty."""
    return resolve_single(product).qty


def compute_expected_states(
    products: list[dict[str, Any]],
    set_resolver: SetResolver,
) -> dict[str, ExpectedVariant]:
    """Return gid to ExpectedVariant for every variant we'll check."""
    expected_by_gid: dict[str, ExpectedVariant] = {}
    singles_by_title = {p["title"]: _single_variant_qty_for_product(p) for p in products}

    for product in products:
        title = product["title"]
        if should_skip(title):
            continue
        # Skip the Daily Facial Cleanser/Moisturizer special case: no PDP to crawl
        if title in ("Daily Facial Cleanser", "Daily Facial Moisturizer"):
            continue

        is_set = set_resolver.is_set(title)

        for variant in product["variants"]:
            gid = variant["id"]
            policy = variant.get("inventoryPolicy", "DENY")
            variant_title = variant.get("title") or ""
            lower = variant_title.lower()

            if is_set:
                # Set variants resolve to min component single
                capacity = set_resolver.capacity(title, singles_by_title)
                sellable = capacity > 0
            elif "two pack" in lower or "2 pack" in lower:
                single_qty = singles_by_title.get(title, 0)
                sellable = (single_qty // 2) >= 1
            elif "three pack" in lower or "3 pack" in lower:
                single_qty = singles_by_title.get(title, 0)
                sellable = (single_qty // 3) >= 1
            elif any(x in lower for x in ("just", "single")) or lower in (
                "default title",
                "full size",
            ):
                sellable = _sellable_from_levels(variant)
            else:
                # Unknown variant shape: fall back to its own availability
                sellable = _sellable_from_levels(variant)

            expected_by_gid[gid] = ExpectedVariant(
                variant_gid=gid,
                product_title=title,
                variant_label=variant_title,
                expected=ExpectedState(sellable=sellable, inventory_policy=policy),
            )

    return expected_by_gid


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

    # Sort: OVERSELL_RISK first (most urgent), then SALES_LEAK, then NO_BUY_BUTTON
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


def _extract_pdp_handle(url: str) -> str | None:
    """Return product handle if URL matches `/products/{handle}` pattern."""
    marker = "/products/"
    if marker not in url:
        return None
    tail = url.split(marker, 1)[1]
    handle = tail.split("/")[0].split("?")[0]
    return handle or None


def _match_variant(
    obs: VariantObservation, candidates: list[ExpectedVariant]
) -> ExpectedVariant | None:
    """Match an observation to an expected variant by variant_label (case-insensitive)."""
    if not candidates:
        return None
    if obs.variant_label is None:
        # Default view (no variant selected): pick the single variant if unique,
        # or the "Just One" / "Default Title" / "Full Size" variant by convention.
        defaults = [
            ev
            for ev in candidates
            if any(
                x in ev.variant_label.lower()
                for x in ("just", "single", "default title", "full size")
            )
        ]
        if defaults:
            return defaults[0]
        return candidates[0] if len(candidates) == 1 else None

    label = obs.variant_label.lower().strip()
    for ev in candidates:
        if ev.variant_label.lower().strip() == label:
            return ev
    # Partial match fallback (e.g., "Santal Sandalwood" obs vs "Santal Sandalwood / Just One" expected)
    for ev in candidates:
        if label in ev.variant_label.lower() or ev.variant_label.lower() in label:
            return ev
    return None


def _run(cfg: Config) -> None:
    token = fetch_access_token(cfg.shopify_store, cfg.shopify_client_id, cfg.shopify_client_secret)
    shopify = ShopifyClient(cfg.shopify_store, token, cfg.shopify_api_version)
    set_resolver = SetResolver(COMPONENTS_PATH)
    state = AlertState.load(cfg.state_path)
    slack = SlackClient(cfg.slack_bot_token, cfg.slack_channel, dry_run=cfg.dry_run)
    url_enum = UrlEnumerator(f"https://{cfg.shopify_store.replace('.myshopify.com', '.com')}")
    # Note: UrlEnumerator uses the public storefront domain. For Based, that's basedbodyworks.com.
    # Adjust if dev store uses a different public domain.

    logger.info("Fetching Shopify products")
    products = shopify.fetch_all_products()
    expected = compute_expected_states(products, set_resolver)

    # Index variants by (product_handle, variant_title_lower) for PDP-variant matching
    handle_to_variants: dict[str, list[ExpectedVariant]] = {}
    for product in products:
        handle = product.get("handle")
        if not handle:
            continue
        handle_to_variants[handle] = [
            ev for ev in expected.values() if ev.product_title == product["title"]
        ]

    logger.info("Enumerating URLs")
    urls = url_enum.enumerate_all()
    all_urls = list(dict.fromkeys(urls.pdp + urls.collection + urls.landing))
    logger.info("Auditing %d URLs", len(all_urls))

    all_flags: list[Flag] = []
    with AtcCrawler(headless=True) as crawler:
        for url in all_urls:
            try:
                observations = crawler.audit_url(url)
            except Exception as exc:
                logger.warning("Crawl failed for %s: %s", url, exc)
                continue

            handle = _extract_pdp_handle(url)
            if handle and handle in handle_to_variants:
                # PDP: match observations to expected variants by variant_label
                pdp_variants = handle_to_variants[handle]
                for obs in observations:
                    ev = _match_variant(obs, pdp_variants)
                    if ev is None:
                        continue
                    all_flags.extend(
                        generate_flags(
                            expected=ev.expected,
                            observed=obs,
                            variant_gid=ev.variant_gid,
                            product_title=ev.product_title,
                        )
                    )
            else:
                # Non-PDP URL (collection or landing page): v0 only flags NO_BUY_BUTTON
                # when the entire page has zero ATC elements. Full product-card matching
                # is a v0.5 enhancement.
                if all(not obs.present for obs in observations):
                    # Page rendered but no ATC found at all. Synthesize a page-level flag.
                    all_flags.append(
                        Flag(
                            flag_type=FlagType.NO_BUY_BUTTON,
                            product_title="(page-level, no ATC detected)",
                            variant_gid=f"page::{url}",
                            variant_label=None,
                            url=url,
                            expected_sellable=True,
                            observed_text="",
                            state_key=f"page::{url}::NO_BUY_BUTTON",
                        )
                    )

    logger.info("Found %d flags", len(all_flags))

    # Dedup: only post NEW flags
    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    new_flags = _dedupe_flags_by_state_key(
        [f for f in all_flags if state.is_new_atc_flag(f.state_key)]
    )

    # Update state with ALL current flags (including persistent ones), prune resolved
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
