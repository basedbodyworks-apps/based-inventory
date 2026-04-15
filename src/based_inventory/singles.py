"""Singles-variant resolver.

Based products have variants (Just One, Two Pack, Three Pack, multi-scent bundles).
Only the "single" variant controls inventory reality:
- Multi-packs pull from singles at fulfillment time
- A 2-pack shown with qty 50 and singles at 0 is unfulfillable

See INVENTORY-RULES.md in the original Inventory Brain for canonical rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SingleResult:
    qty: int
    breakdown: str | None  # populated for multi-scent sums
    source_variants: list[dict[str, Any]]  # the variants that contributed to the sum


def _is_single_variant(variant: dict[str, Any]) -> bool:
    title = (variant.get("title") or "").lower()
    sku = (variant.get("sku") or "").lower()
    if title in ("default title", "full size"):
        return True
    if "just" in title or "single" in title:
        return True
    return "single" in sku


def resolve_single(product: dict[str, Any]) -> SingleResult:
    variants = product.get("variants", [])

    if len(variants) == 1:
        v = variants[0]
        return SingleResult(qty=v.get("inventoryQuantity", 0), breakdown=None, source_variants=[v])

    singles = [v for v in variants if _is_single_variant(v)]

    if not singles:
        return SingleResult(
            qty=product.get("totalInventory", 0),
            breakdown=None,
            source_variants=[],
        )

    if len(singles) == 1:
        v = singles[0]
        return SingleResult(
            qty=v.get("inventoryQuantity", 0), breakdown=None, source_variants=singles
        )

    # Multi-scent: sum and build breakdown string
    total = sum(v.get("inventoryQuantity", 0) for v in singles)
    parts = []
    for v in sorted(singles, key=lambda x: x.get("inventoryQuantity", 0)):
        title = v.get("title", "")
        scent = title.split("/")[0].strip() if "/" in title else title
        parts.append(f"{scent}: {v.get('inventoryQuantity', 0):,}")
    breakdown = " | ".join(parts)
    return SingleResult(qty=total, breakdown=breakdown, source_variants=singles)
