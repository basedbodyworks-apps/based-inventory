"""Every 6h: scan Shopify inventory, post tier-escalation alerts to Slack.

Ported from Inventory Brain's check_inventory.py with:
- Shared modules (config, shopify, singles, sets, state, slack, telegram)
- New type hints and tests
- Telegram via HTTP, not openclaw CLI
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from based_inventory.auth import fetch_access_token
from based_inventory.config import Config
from based_inventory.jobs._common import run_job
from based_inventory.sets import SetResolver
from based_inventory.shopify import ShopifyClient
from based_inventory.singles import resolve_single
from based_inventory.skip_list import should_skip
from based_inventory.slack import SlackClient, context, divider, header, section
from based_inventory.state import AlertState

# Threshold ladder (ported from check_inventory.py:39-44).
# Individual @-mentions intentionally omitted; <!channel> still fires below.
THRESHOLDS: list[tuple[int, str]] = [
    (100, "🚨 CRITICAL"),
    (500, "🔴 LOW STOCK"),
    (750, "🟠 WARNING"),
    (1000, "🟡 HEADS UP"),
]

STORE_ADMIN = "https://admin.shopify.com/store/basedbodyworks"
COMPONENTS_PATH = Path(__file__).resolve().parents[3] / "data" / "set-components.json"


@dataclass
class Alert:
    label: str
    product_title: str
    qty: int
    threshold: int
    variant_info: str
    admin_url: str
    affected_sets: list[str]


def _tier_for(qty: int) -> tuple[int, str] | None:
    for threshold, label in THRESHOLDS:
        if qty <= threshold:
            return threshold, label
    return None


def _product_id(gid: str) -> str:
    return gid.rsplit("/", 1)[-1]


def build_blocks(alerts: list[Alert]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        header("⚡ Inventory Alert"),
        divider(),
    ]

    for a in alerts:
        text = (
            f"{a.label}  •  <{a.admin_url}|*{a.product_title}*>\n"
            f"📦  *{a.qty:,}* singles remaining"
        )
        if a.variant_info:
            text += f"\n{a.variant_info}"
        if a.affected_sets:
            text += f"\n⚠️  _Bottleneck for: {', '.join(a.affected_sets)}_"
        blocks.append(section(text))

    blocks.append(divider())

    ts = time.strftime("%b %d, %I:%M %p PST", time.gmtime(time.time() - 7 * 3600))
    blocks.append(context(f"🕐  {ts}"))
    return blocks


def _run(cfg: Config) -> None:
    token = fetch_access_token(cfg.shopify_store, cfg.shopify_client_id, cfg.shopify_client_secret)
    shopify = ShopifyClient(cfg.shopify_store, token, cfg.shopify_api_version)
    set_resolver = SetResolver(COMPONENTS_PATH)
    state = AlertState.load(cfg.state_path)
    slack = SlackClient(cfg.slack_bot_token, cfg.slack_channel, dry_run=cfg.dry_run)

    products = shopify.fetch_all_products()
    alerts: list[Alert] = []
    new_tiers: dict[str, int] = {}

    for product in products:
        title = product["title"]

        if should_skip(title):
            continue
        if set_resolver.is_set(title):
            continue

        result = resolve_single(product)
        if result.qty < 0:
            continue

        tier = _tier_for(result.qty)
        if tier is None:
            state.clear_tier(title)
            continue

        threshold, label = tier
        new_tiers[title] = threshold

        if state.crosses_lower_tier(title, threshold):
            variant_info = (
                f"`{result.source_variants[0]['sku']}`"
                if (result.source_variants and result.source_variants[0].get("sku"))
                else ""
            )
            if result.breakdown:
                variant_info = result.breakdown
            alerts.append(
                Alert(
                    label=label,
                    product_title=title,
                    qty=result.qty,
                    threshold=threshold,
                    variant_info=variant_info,
                    admin_url=f"{STORE_ADMIN}/products/{_product_id(product['id'])}",
                    affected_sets=set_resolver.sets_containing(title),
                )
            )

    # Update state: overwrite quantity_tiers with the current scan's active tiers
    state.quantity_tiers = new_tiers
    state.save(cfg.state_path)

    if not alerts:
        return

    alerts.sort(key=lambda a: a.qty)
    blocks = build_blocks(alerts)
    fallback = f"⚡ Inventory Alert: {len(alerts)} product(s) below threshold"
    slack.post_message(fallback, blocks)


def main() -> None:
    run_job("quantity_alerts", _run)


if __name__ == "__main__":
    main()
