"""Products excluded from all alerting and auditing.

Merged from Inventory Brain's check_inventory.py SKIP_TITLES and INVENTORY-RULES.md.
Archived products are already filtered by the Shopify query (status:active).
"""

from __future__ import annotations

_SKIP_TITLES: frozenset[str] = frozenset(
    {
        # Shipping and fulfillment
        "Shipping",
        "Shipping International",
        "BASED Shipping Protection",
        # Membership and brand
        "Based Membership",
        "Brand Ambassador Package",
        # Samples
        "Shampoo + Conditioner Bundle Sample",
        "4oz Shampoo + Conditioner Bundle Sample",
        # Accessories (legacy or off-catalog)
        "Bath Stone (White)",
        "Based Wooden Comb - Light",
        "Based Wooden Comb",
        # Legacy formulations (retired product lines)
        "Based Shampoo 1.0",
        "Based Shampoo 2.0",
        "Based Conditioner 1.0",
        "Based Conditioner 2.0",
        "Hair Revival Serum",
        "Super Serum",
        "Showerhead Filter",
        # TikTok Shop exclusives. These are live SKUs with their own inventory
        # but are not sold on basedbodyworks.com. Their /products/{handle}
        # PDPs inject an Instant Commerce redirect to /pages/not-found.
        # Monitored via TikTok Shop's own channel, not this bot.
        "Texture & Style Duo",
        "Shower Duo + Hair Elixir",
        "Deluxe Straight/Wavy Hair Kit",
        "Santal Bodycare Essentials",
    }
)


def should_skip(product_title: str) -> bool:
    return product_title in _SKIP_TITLES
