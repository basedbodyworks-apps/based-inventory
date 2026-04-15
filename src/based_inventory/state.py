"""Persistent alert state for dedup across runs.

Stores the last-observed severity tier per product (quantity alerts)
and the set of currently-flagged ATC anomalies (ATC audit).

File format:
{
  "quantity_tiers": {"Shampoo": 500, "Conditioner": 1000},
  "atc_flags": {
    "<variant_gid>::<url>::<flag_type>": {"first_seen_at": "...", "last_seen_at": "..."}
  }
}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AlertState:
    quantity_tiers: dict[str, int] = field(default_factory=dict)
    atc_flags: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> AlertState:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load state from %s: %s; starting fresh", p, exc)
            return cls()
        return cls(
            quantity_tiers=data.get("quantity_tiers", {}),
            atc_flags=data.get("atc_flags", {}),
        )

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "quantity_tiers": self.quantity_tiers,
                    "atc_flags": self.atc_flags,
                },
                indent=2,
            )
        )

    # Quantity tier API
    def get_tier(self, product_title: str) -> int | None:
        return self.quantity_tiers.get(product_title)

    def set_tier(self, product_title: str, tier: int) -> None:
        self.quantity_tiers[product_title] = tier

    def clear_tier(self, product_title: str) -> None:
        self.quantity_tiers.pop(product_title, None)

    def crosses_lower_tier(self, product_title: str, new_tier: int) -> bool:
        """True if new_tier represents worse state than previously recorded."""
        prev = self.get_tier(product_title)
        if prev is None:
            return True
        return new_tier < prev

    # ATC flag API
    def is_new_atc_flag(self, key: str) -> bool:
        return key not in self.atc_flags

    def mark_atc_flag(self, key: str, now: str) -> None:
        if key in self.atc_flags:
            self.atc_flags[key]["last_seen_at"] = now
        else:
            self.atc_flags[key] = {"first_seen_at": now, "last_seen_at": now}

    def retain_only_atc_flags(self, keep_keys: set[str]) -> None:
        """Drop ATC flags not in keep_keys (used after a full audit run)."""
        self.atc_flags = {k: v for k, v in self.atc_flags.items() if k in keep_keys}
