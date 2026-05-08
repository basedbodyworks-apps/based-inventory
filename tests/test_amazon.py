"""Tests for amazon.py (SP-API client) and amazon_auth.py (LWA token exchange)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from based_inventory.amazon import (
    NA_HOST,
    US_MARKETPLACE_ID,
    AmazonSPClient,
    FbaInventorySummary,
    _parse_summary,
)
from based_inventory.amazon_auth import LWA_TOKEN_URL, fetch_access_token

# --------------------------------------------------------------------------
# LWA token exchange
# --------------------------------------------------------------------------


@patch("based_inventory.amazon_auth.requests.post")
def test_fetch_access_token_returns_access_token_on_success(mock_post: MagicMock) -> None:
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"access_token": "Atza|aaa", "expires_in": 3600},
    )
    token = fetch_access_token("Atzr|refresh", "amzn1.application-oa2-client.cid", "secret")
    assert token == "Atza|aaa"
    args, kwargs = mock_post.call_args
    assert args[0] == LWA_TOKEN_URL
    assert kwargs["data"]["grant_type"] == "refresh_token"
    assert kwargs["data"]["refresh_token"] == "Atzr|refresh"


@patch("based_inventory.amazon_auth.requests.post")
def test_fetch_access_token_raises_on_http_error(mock_post: MagicMock) -> None:
    mock_post.return_value = MagicMock(status_code=400, text="invalid_grant")
    with pytest.raises(RuntimeError, match="HTTP 400"):
        fetch_access_token("bad", "cid", "secret")


@patch("based_inventory.amazon_auth.requests.post")
def test_fetch_access_token_raises_on_missing_token_in_response(mock_post: MagicMock) -> None:
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"foo": "bar"})
    with pytest.raises(RuntimeError, match="missing access_token"):
        fetch_access_token("r", "c", "s")


# --------------------------------------------------------------------------
# Summary parsing
# --------------------------------------------------------------------------


def _raw_summary(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "asin": "B0EXAMPLE01",
        "fnSku": "X001ABCDEF",
        "sellerSku": "BB-CC-SINGLE",
        "condition": "NewItem",
        "lastUpdatedTime": "2026-05-07T10:00:00Z",
        "productName": "Curl Cream",
        "totalQuantity": 100,
        "inventoryDetails": {
            "fulfillableQuantity": 75,
            "inboundWorkingQuantity": 0,
            "inboundShippedQuantity": 20,
            "inboundReceivingQuantity": 0,
            "reservedQuantity": {
                "totalReservedQuantity": 5,
                "pendingCustomerOrderQuantity": 5,
                "pendingTransshipmentQuantity": 0,
                "fcProcessingQuantity": 0,
            },
            "researchingQuantity": {"totalResearchingQuantity": 0},
            "unfulfillableQuantity": {
                "totalUnfulfillableQuantity": 3,
                "customerDamagedQuantity": 1,
                "warehouseDamagedQuantity": 2,
            },
        },
    }
    base.update(overrides)
    return base


def test_parse_summary_extracts_full_breakdown() -> None:
    s = _parse_summary(_raw_summary())
    assert s.seller_sku == "BB-CC-SINGLE"
    assert s.asin == "B0EXAMPLE01"
    assert s.fn_sku == "X001ABCDEF"
    assert s.product_name == "Curl Cream"
    assert s.fulfillable == 75
    assert s.inbound_shipped == 20
    assert s.reserved_total == 5
    assert s.unsellable_total == 3


def test_parse_summary_handles_missing_inventory_details() -> None:
    raw = _raw_summary()
    raw["inventoryDetails"] = None
    s = _parse_summary(raw)
    assert s.fulfillable == 0
    assert s.inbound_shipped == 0
    assert s.reserved_total == 0
    assert s.unsellable_total == 0


def test_parse_summary_preserves_raw_for_debugging() -> None:
    raw = _raw_summary()
    s = _parse_summary(raw)
    assert s.raw is raw


# --------------------------------------------------------------------------
# Client requests
# --------------------------------------------------------------------------


def test_client_default_host_and_marketplace_are_us_na() -> None:
    c = AmazonSPClient(access_token="Atza|x")
    assert c.host == NA_HOST
    assert c.marketplace_id == US_MARKETPLACE_ID


def test_client_passes_access_token_in_header() -> None:
    c = AmazonSPClient(access_token="Atza|secret")
    headers = c._headers()
    assert headers["x-amz-access-token"] == "Atza|secret"
    assert headers["Accept"] == "application/json"


def test_fetch_fba_inventory_summaries_rejects_oversized_sku_filter() -> None:
    c = AmazonSPClient(access_token="t")
    with pytest.raises(ValueError, match="capped at 50"):
        c.fetch_fba_inventory_summaries(seller_skus=[f"sku-{i}" for i in range(51)])


@patch("based_inventory.amazon.requests.get")
def test_fetch_fba_inventory_summaries_walks_pagination(mock_get: MagicMock) -> None:
    page1 = {
        "payload": {
            "inventorySummaries": [_raw_summary(sellerSku="A", asin="B01")],
            "nextToken": "TOKEN_PAGE_2",
        }
    }
    page2 = {"payload": {"inventorySummaries": [_raw_summary(sellerSku="B", asin="B02")]}}
    mock_get.side_effect = [
        MagicMock(status_code=200, json=lambda: page1),
        MagicMock(status_code=200, json=lambda: page2),
    ]
    c = AmazonSPClient(access_token="t", pace_seconds=0)
    out = c.fetch_fba_inventory_summaries()
    assert len(out) == 2
    assert {s.seller_sku for s in out} == {"A", "B"}
    # Second call should pass nextToken
    second_call_kwargs = mock_get.call_args_list[1]
    assert second_call_kwargs.kwargs["params"]["nextToken"] == "TOKEN_PAGE_2"


@patch("based_inventory.amazon.requests.get")
def test_fetch_fba_inventory_summaries_passes_filter_skus(mock_get: MagicMock) -> None:
    mock_get.return_value = MagicMock(
        status_code=200, json=lambda: {"payload": {"inventorySummaries": []}}
    )
    c = AmazonSPClient(access_token="t", pace_seconds=0)
    c.fetch_fba_inventory_summaries(seller_skus=["BB-CC-SINGLE", "BB-SHMP"])
    params = mock_get.call_args.kwargs["params"]
    assert params["sellerSkus"] == "BB-CC-SINGLE,BB-SHMP"
    assert params["granularityType"] == "Marketplace"
    assert params["marketplaceIds"] == US_MARKETPLACE_ID


@patch("based_inventory.amazon.requests.get")
def test_client_raises_runtimeerror_on_unexpected_http_status(mock_get: MagicMock) -> None:
    # 4xx (non-429) should bubble up immediately, not retry forever
    mock_get.return_value = MagicMock(status_code=403, text="Forbidden")
    c = AmazonSPClient(access_token="t", pace_seconds=0)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        c.fetch_fba_inventory_summaries()


@patch("based_inventory.amazon.time.sleep")
@patch("based_inventory.amazon.requests.get")
def test_client_retries_on_429_and_eventually_succeeds(
    mock_get: MagicMock, mock_sleep: MagicMock
) -> None:
    rate_limited = MagicMock(
        status_code=429,
        headers={"x-amzn-RateLimit-Limit": "0.5"},
        text="rate-limited",
    )
    success = MagicMock(
        status_code=200,
        json=lambda: {"payload": {"inventorySummaries": []}},
    )
    mock_get.side_effect = [rate_limited, success]
    c = AmazonSPClient(access_token="t", pace_seconds=0)
    result = c.fetch_fba_inventory_summaries()
    assert result == []
    assert mock_get.call_count == 2  # one rate-limited, one success
    assert mock_sleep.called


# --------------------------------------------------------------------------
# Dataclass shape
# --------------------------------------------------------------------------


def test_fba_inventory_summary_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    s = FbaInventorySummary(
        seller_sku="X",
        asin="B0",
        fn_sku=None,
        product_name="P",
        condition="NewItem",
        last_updated_time=None,
        fulfillable=0,
        inbound_working=0,
        inbound_shipped=0,
        inbound_receiving=0,
        reserved_total=0,
        researching=0,
        unsellable_total=0,
        raw={},
    )
    with pytest.raises(FrozenInstanceError):
        s.seller_sku = "Y"  # type: ignore[misc]
