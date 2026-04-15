"""Virtual-bundle (set) resolution.

Sets are Shopify products with their own PDPs but no real inventory.
Set capacity = min(component single qty). If any component is at 0,
the set is unfulfillable even if its own inventoryQuantity is positive.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


class SetResolver:
    def __init__(self, components_path: Path | str) -> None:
        data = json.loads(Path(components_path).read_text())
        self._sets: dict[str, list[str]] = data.get("sets", {})
        self._reverse: dict[str, list[str]] = defaultdict(list)
        for set_name, components in self._sets.items():
            for component in components:
                self._reverse[component].append(set_name)

    def is_set(self, product_title: str) -> bool:
        return product_title in self._sets

    def components_for(self, set_name: str) -> list[str]:
        return list(self._sets[set_name])

    def sets_containing(self, component: str) -> list[str]:
        return list(self._reverse.get(component, []))

    def capacity(self, set_name: str, singles_by_title: dict[str, int]) -> int:
        components = self._sets[set_name]
        return min((singles_by_title.get(c, 0) for c in components), default=0)

    def all_set_names(self) -> list[str]:
        return list(self._sets.keys())
