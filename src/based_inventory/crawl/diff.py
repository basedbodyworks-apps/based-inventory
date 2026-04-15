"""Expected-vs-observed ATC state diff, flag generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from based_inventory.crawl.atc import VariantObservation


class FlagType(str, Enum):
    SALES_LEAK = "SALES_LEAK"
    OVERSELL_RISK = "OVERSELL_RISK"
    NO_BUY_BUTTON = "NO_BUY_BUTTON"


@dataclass(frozen=True)
class ExpectedState:
    sellable: bool
    inventory_policy: str  # "DENY" or "CONTINUE"


@dataclass(frozen=True)
class Flag:
    flag_type: FlagType
    product_title: str
    variant_gid: str
    variant_label: str | None
    url: str
    expected_sellable: bool
    observed_text: str
    state_key: str


def _make_key(variant_gid: str, url: str, flag_type: FlagType) -> str:
    return f"{variant_gid}::{url}::{flag_type.value}"


def _is_sold_out_text(text: str) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in ("sold out", "notify me", "coming soon", "unavailable"))


def generate_flags(
    expected: ExpectedState,
    observed: VariantObservation,
    variant_gid: str,
    product_title: str,
) -> list[Flag]:
    """Return list of flags for this (expected, observed) pair. Empty list if state matches."""
    # NO_BUY_BUTTON always wins when ATC element is missing
    if not observed.present:
        return [
            Flag(
                flag_type=FlagType.NO_BUY_BUTTON,
                product_title=product_title,
                variant_gid=variant_gid,
                variant_label=observed.variant_label,
                url=observed.url,
                expected_sellable=expected.sellable,
                observed_text=observed.text,
                state_key=_make_key(variant_gid, observed.url, FlagType.NO_BUY_BUTTON),
            )
        ]

    observed_sellable = observed.enabled and not _is_sold_out_text(observed.text)

    if expected.sellable and not observed_sellable:
        return [
            Flag(
                flag_type=FlagType.SALES_LEAK,
                product_title=product_title,
                variant_gid=variant_gid,
                variant_label=observed.variant_label,
                url=observed.url,
                expected_sellable=expected.sellable,
                observed_text=observed.text,
                state_key=_make_key(variant_gid, observed.url, FlagType.SALES_LEAK),
            )
        ]

    if not expected.sellable and observed_sellable:
        if expected.inventory_policy.upper() == "CONTINUE":
            # Backorder allowed; ATC enabled when OOS is intentional
            return []
        return [
            Flag(
                flag_type=FlagType.OVERSELL_RISK,
                product_title=product_title,
                variant_gid=variant_gid,
                variant_label=observed.variant_label,
                url=observed.url,
                expected_sellable=expected.sellable,
                observed_text=observed.text,
                state_key=_make_key(variant_gid, observed.url, FlagType.OVERSELL_RISK),
            )
        ]

    return []
