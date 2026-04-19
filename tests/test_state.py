"""Tests for persistent alert state."""

from pathlib import Path
from unittest.mock import patch

import fakeredis

from based_inventory.state import REDIS_STATE_KEY, AlertState


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


def test_load_wrong_shape_returns_empty(tmp_path: Path):
    path = tmp_path / "wrong.json"
    path.write_text("[1, 2, 3]")
    state = AlertState.load(path)
    assert state.quantity_tiers == {}
    assert state.atc_flags == {}


def test_load_wrong_value_types_returns_empty(tmp_path: Path):
    path = tmp_path / "weird.json"
    path.write_text('{"quantity_tiers": "oops", "atc_flags": [1,2]}')
    state = AlertState.load(path)
    assert state.quantity_tiers == {}
    assert state.atc_flags == {}


def test_clear_atc_flags_not_in_set(tmp_path: Path):
    state = AlertState.load(tmp_path / "s.json")
    state.mark_atc_flag("k1", now="2026-04-15T06:00:00Z")
    state.mark_atc_flag("k2", now="2026-04-15T06:00:00Z")

    state.retain_only_atc_flags({"k1"})

    assert state.is_new_atc_flag("k1") is False
    assert state.is_new_atc_flag("k2") is True


# Redis backend tests


def _fake_redis_patch():
    """Patch redis.from_url to return a fakeredis client."""
    server = fakeredis.FakeServer()

    def _factory(url, *args, **kwargs):
        del url, args
        return fakeredis.FakeRedis(
            server=server, decode_responses=kwargs.get("decode_responses", False)
        )

    return patch("redis.from_url", side_effect=_factory), server


def test_redis_load_missing_key_returns_empty():
    patcher, _server = _fake_redis_patch()
    with patcher:
        state = AlertState.load("redis://localhost:6379/0")
    assert state.quantity_tiers == {}
    assert state.atc_flags == {}


def test_redis_save_and_reload():
    patcher, server = _fake_redis_patch()
    with patcher:
        state = AlertState.load("redis://localhost:6379/0")
        state.set_tier("Shampoo", 500)
        state.mark_atc_flag("k1", now="2026-04-15T06:00:00Z")
        state.save("redis://localhost:6379/0")

        reloaded = AlertState.load("redis://localhost:6379/0")

    assert reloaded.get_tier("Shampoo") == 500
    assert reloaded.is_new_atc_flag("k1") is False

    # Verify payload shape in Redis directly
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    raw = client.get(REDIS_STATE_KEY)
    assert raw is not None
    assert '"quantity_tiers"' in raw
    assert '"atc_flags"' in raw


def test_redis_malformed_json_returns_empty():
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    client.set(REDIS_STATE_KEY, "not valid json")

    def _factory(url, *args, **kwargs):
        del url, args
        return fakeredis.FakeRedis(
            server=server, decode_responses=kwargs.get("decode_responses", False)
        )

    with patch("redis.from_url", side_effect=_factory):
        state = AlertState.load("redis://localhost:6379/0")

    assert state.quantity_tiers == {}
    assert state.atc_flags == {}


def test_redis_url_with_tls_scheme_dispatches_to_redis():
    patcher, _server = _fake_redis_patch()
    with patcher:
        state = AlertState.load("rediss://localhost:6379/0")
    assert state.quantity_tiers == {}
