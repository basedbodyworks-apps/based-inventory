"""Fridays 9am PST: post full inventory snapshot to Slack.

Ported from Inventory Brain's weekly_audit.py. Tracks 23 products across
6 categories at single-variant level. Sets excluded (constrained by lowest component).
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
from based_inventory.slack import SlackClient, context, divider, header, section

LOW = 1000

# Same layout as weekly_audit.py:22-29. 'special' indicates variant-of-Daily-Skincare-Duo.
AUDIT_LAYOUT: list[tuple[str, list[tuple[str, str | None]]]] = [
    ("Hair Care", [("Shampoo", None), ("Conditioner", None), ("Hair Elixir", None)]),
    (
        "Straight/Wavy Styling",
        [("Texture Powder", None), ("Sea Salt Spray", None), ("Pomade", None), ("Hair Clay", None)],
    ),
    (
        "Curly Styling",
        [
            ("Leave-In Conditioner", None),
            ("Curl Cream", None),
            ("Curl Mousse", None),
            ("Curl Gel", None),
            ("Curl Refresh Spray", None),
        ],
    ),
    ("Body", [("Body Wash", None), ("Body Lotion", None), ("Deodorant", None)]),
    (
        "Skin",
        [
            ("Facial Cleanser", "special"),
            ("Facial Moisturizer", "special"),
            ("Skin Revival Spray", None),
            ("Under Eye Elixir", None),
            ("Tallow Moisturizer", None),
        ],
    ),
    ("Accessories", [("Toiletry Bag", None), ("Scalp Scrubber", None), ("Wooden Hair Comb", None)]),
]

COMPONENTS_PATH = Path(__file__).resolve().parents[3] / "data" / "set-components.json"


@dataclass
class ProductLine:
    name: str
    qty: int
    breakdown: str | None
    pack2: int | None
    affected_sets: list[str]


def _emoji(qty: int) -> str:
    if qty < 0:
        return "⛔"
    if qty <= 100:
        return "🚨"
    if qty <= 500:
        return "🔴"
    if qty <= 750:
        return "🟠"
    if qty <= 1000:
        return "🟡"
    if qty <= 5000:
        return "📊"
    return "🟢"


def _twopack_qty(product: dict[str, Any]) -> int | None:
    twos = [
        v
        for v in product["variants"]
        if any(p in (v.get("title") or "").lower() for p in ("two pack", "pack of 2", "2pck"))
        and "just" not in (v.get("title") or "").lower()
        and "single" not in (v.get("title") or "").lower()
    ]
    if not twos:
        return None
    return sum(v.get("inventoryQuantity", 0) for v in twos)


def _render_line(line: ProductLine) -> str:
    text = f"{_emoji(line.qty)} {line.name}: *{line.qty:,}*"
    if line.breakdown:
        text += f"  ({line.breakdown})"
    if 0 < line.qty <= LOW and line.pack2 is not None:
        text += f"  ·  2-pack: {line.pack2:,}"
    if line.qty <= LOW and line.affected_sets:
        text += f" -> {', '.join(line.affected_sets)}"
    return text


def build_snapshot_blocks(
    sections: list[tuple[str, list[ProductLine]]], date_str: str
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        header("📦 Weekly Inventory Audit"),
        section(
            "Tracking *23* products at *single-variant level* (not inflated totals)\n"
            "Sets excluded; constrained by lowest component  |  🗓️ " + date_str
        ),
        divider(),
    ]

    for category, lines in sections:
        body = "*" + category + "*\n" + "\n".join(_render_line(line) for line in lines)
        blocks.append(section(body))

    blocks.append(divider())
    blocks.append(
        context(
            "🟢 5K+  ·  📊 1K-5K  ·  🟡 ≤1K  ·  🟠 ≤750  ·  🔴 ≤500  ·  🚨 ≤100  ·  ⛔ Oversold\n<!channel>"
        )
    )
    return blocks


def _run(cfg: Config) -> None:
    token = fetch_access_token(cfg.shopify_store, cfg.shopify_client_id, cfg.shopify_client_secret)
    shopify = ShopifyClient(cfg.shopify_store, token, cfg.shopify_api_version)
    set_resolver = SetResolver(COMPONENTS_PATH)
    slack = SlackClient(cfg.slack_bot_token, cfg.slack_channel, dry_run=cfg.dry_run)

    products = shopify.fetch_all_products()
    by_title = {p["title"]: p for p in products}

    # Daily Facial Cleanser / Moisturizer live as variants of Daily Skincare Duo
    dsd = by_title.get("Daily Skincare Duo")
    facial_cleanser_qty: int | None = None
    facial_moisturizer_qty: int | None = None
    if dsd:
        for v in dsd.get("variants", []):
            lower = (v.get("title") or "").lower()
            if "cleanser" in lower:
                facial_cleanser_qty = v.get("inventoryQuantity")
            elif "moisturizer" in lower:
                facial_moisturizer_qty = v.get("inventoryQuantity")

    sections: list[tuple[str, list[ProductLine]]] = []
    for category, entries in AUDIT_LAYOUT:
        lines: list[ProductLine] = []
        for name, special in entries:
            if special == "special":
                qty = facial_cleanser_qty if name == "Facial Cleanser" else facial_moisturizer_qty
                rev_key = (
                    "Daily Facial Cleanser"
                    if name == "Facial Cleanser"
                    else "Daily Facial Moisturizer"
                )
                if qty is None:
                    lines.append(
                        ProductLine(
                            name=f"❓ {name}: not found",
                            qty=0,
                            breakdown=None,
                            pack2=None,
                            affected_sets=[],
                        )
                    )
                    continue
                lines.append(
                    ProductLine(
                        name=name,
                        qty=qty,
                        breakdown=None,
                        pack2=None,
                        affected_sets=set_resolver.sets_containing(rev_key),
                    )
                )
                continue

            product = by_title.get(name)
            if not product:
                lines.append(
                    ProductLine(
                        name=f"❓ {name}: not found",
                        qty=0,
                        breakdown=None,
                        pack2=None,
                        affected_sets=[],
                    )
                )
                continue

            result = resolve_single(product)
            lines.append(
                ProductLine(
                    name=name,
                    qty=result.qty,
                    breakdown=result.breakdown,
                    pack2=_twopack_qty(product) if 0 < result.qty <= LOW else None,
                    affected_sets=set_resolver.sets_containing(name),
                )
            )

        sections.append((category, lines))

    date_str = time.strftime("%b %d, %Y")
    blocks = build_snapshot_blocks(sections, date_str)
    fallback = f"📦 Weekly Inventory Audit: {date_str}"
    slack.post_message(fallback, blocks)


def main() -> None:
    run_job("weekly_snapshot", _run)


if __name__ == "__main__":
    main()
