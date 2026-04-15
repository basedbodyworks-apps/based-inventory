"""Tests for virtual-bundle resolution."""

import json
from pathlib import Path

import pytest

from based_inventory.sets import SetResolver


@pytest.fixture
def resolver(tmp_path: Path) -> SetResolver:
    components_file = tmp_path / "set-components.json"
    components_file.write_text(
        json.dumps(
            {
                "sets": {
                    "Shower Duo": ["Shampoo", "Conditioner"],
                    "Curly Duo": ["Curl Cream", "Leave-In Conditioner"],
                    "Daily Skincare Duo": ["Daily Facial Cleanser", "Daily Facial Moisturizer"],
                }
            }
        )
    )
    return SetResolver(components_path=components_file)


def test_is_set_returns_true_for_known_set(resolver: SetResolver) -> None:
    assert resolver.is_set("Shower Duo") is True
    assert resolver.is_set("Shampoo") is False


def test_components_for_set(resolver: SetResolver) -> None:
    assert resolver.components_for("Shower Duo") == ["Shampoo", "Conditioner"]


def test_sets_containing_component(resolver: SetResolver) -> None:
    assert resolver.sets_containing("Shampoo") == ["Shower Duo"]
    assert resolver.sets_containing("Curl Cream") == ["Curly Duo"]
    assert resolver.sets_containing("Nonexistent") == []


def test_set_capacity_uses_min_component(resolver: SetResolver) -> None:
    singles = {"Shampoo": 4000, "Conditioner": 1500}
    assert resolver.capacity("Shower Duo", singles) == 1500


def test_set_capacity_missing_component_returns_zero(resolver: SetResolver) -> None:
    singles = {"Shampoo": 4000}
    assert resolver.capacity("Shower Duo", singles) == 0


def test_set_capacity_for_unknown_set_raises(resolver: SetResolver) -> None:
    with pytest.raises(KeyError):
        resolver.capacity("Unknown Set", {})
