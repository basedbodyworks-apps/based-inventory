"""Tests for env var loading."""

import pytest

from based_inventory.config import Config


def test_config_loads_required_fields(monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE", "basedbodyworks.myshopify.com")
    monkeypatch.setenv("SHOPIFY_TOKEN", "shpat_test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL", "C123")

    cfg = Config.from_env()

    assert cfg.shopify_store == "basedbodyworks.myshopify.com"
    assert cfg.shopify_token == "shpat_test"
    assert cfg.slack_bot_token == "xoxb-test"
    assert cfg.slack_channel == "C123"
    assert cfg.shopify_api_version == "2026-01"
    assert cfg.dry_run is False


def test_config_dry_run_flag(monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE", "x")
    monkeypatch.setenv("SHOPIFY_TOKEN", "x")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "x")
    monkeypatch.setenv("SLACK_CHANNEL", "x")
    monkeypatch.setenv("DRY_RUN", "1")

    cfg = Config.from_env()

    assert cfg.dry_run is True


def test_config_missing_required_field_raises(monkeypatch):
    monkeypatch.delenv("SHOPIFY_STORE", raising=False)
    monkeypatch.setenv("SHOPIFY_TOKEN", "x")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "x")
    monkeypatch.setenv("SLACK_CHANNEL", "x")

    with pytest.raises(ValueError, match="SHOPIFY_STORE"):
        Config.from_env()
