"""Tests for persistent alert state."""

from pathlib import Path

from based_inventory.state import AlertState


def test_load_missing_file_returns_empty(tmp_path: Path):
    state = AlertState.load(tmp_path / "missing.json")
    assert state.quantity_tiers == {}
    assert state.atc_flags == {}


def test_load_malformed_file_returns_empty(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("not json")
    state = AlertState.load(path)
    assert state.quantity_tiers == {}


def test_set_and_get_quantity_tier(tmp_path: Path):
    state = AlertState.load(tmp_path / "s.json")
    state.set_tier("Shampoo", 500)
    assert state.get_tier("Shampoo") == 500
    assert state.get_tier("Unknown") is None


def test_crosses_lower_tier_true_on_drop(tmp_path: Path):
    state = AlertState.load(tmp_path / "s.json")
    state.set_tier("Shampoo", 1000)
    assert state.crosses_lower_tier("Shampoo", 500) is True


def test_crosses_lower_tier_false_on_same(tmp_path: Path):
    state = AlertState.load(tmp_path / "s.json")
    state.set_tier("Shampoo", 500)
    assert state.crosses_lower_tier("Shampoo", 500) is False


def test_crosses_lower_tier_false_on_recovery(tmp_path: Path):
    state = AlertState.load(tmp_path / "s.json")
    state.set_tier("Shampoo", 500)
    assert state.crosses_lower_tier("Shampoo", 1000) is False


def test_first_time_ever_crosses_true(tmp_path: Path):
    state = AlertState.load(tmp_path / "s.json")
    assert state.crosses_lower_tier("NewProduct", 500) is True


def test_atc_flag_new(tmp_path: Path):
    state = AlertState.load(tmp_path / "s.json")
    key = "gid://shopify/ProductVariant/1::/products/x::SALES_LEAK"
    assert state.is_new_atc_flag(key) is True
    state.mark_atc_flag(key, now="2026-04-15T06:00:00Z")
    assert state.is_new_atc_flag(key) is False


def test_save_and_reload(tmp_path: Path):
    path = tmp_path / "s.json"
    state = AlertState.load(path)
    state.set_tier("A", 100)
    state.mark_atc_flag("k1", now="2026-04-15T06:00:00Z")
    state.save(path)

    reloaded = AlertState.load(path)
    assert reloaded.get_tier("A") == 100
    assert reloaded.is_new_atc_flag("k1") is False


def test_clear_atc_flags_not_in_set(tmp_path: Path):
    state = AlertState.load(tmp_path / "s.json")
    state.mark_atc_flag("k1", now="2026-04-15T06:00:00Z")
    state.mark_atc_flag("k2", now="2026-04-15T06:00:00Z")

    state.retain_only_atc_flags({"k1"})

    assert state.is_new_atc_flag("k1") is False
    assert state.is_new_atc_flag("k2") is True
