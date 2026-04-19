"""Tests for Shopify Client Credentials Grant token acquisition."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from based_inventory.auth import fetch_access_token


def _mock_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body or {}
    response.text = text
    return response


def test_fetch_access_token_success():
    with patch("based_inventory.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200,
            {
                "access_token": "shpat_abc123",
                "scope": "read_products,read_inventory,read_locations",
                "expires_in": 86399,
            },
        )

        token = fetch_access_token(
            "basedbodyworks.myshopify.com",
            "client_id_xyz",
            "shpss_secret_xyz",
        )

    assert token == "shpat_abc123"
    mock_post.assert_called_once()
    call = mock_post.call_args
    assert call.args[0] == "https://basedbodyworks.myshopify.com/admin/oauth/access_token"
    assert call.kwargs["json"] == {
        "client_id": "client_id_xyz",
        "client_secret": "shpss_secret_xyz",
        "grant_type": "client_credentials",
    }
    assert call.kwargs["headers"]["Content-Type"] == "application/json"
    assert call.kwargs["headers"]["Accept"] == "application/json"


def test_fetch_access_token_non_200_raises():
    with patch("based_inventory.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            400,
            text='{"error":"shop_not_permitted"}',
        )

        with pytest.raises(RuntimeError, match="HTTP 400"):
            fetch_access_token("store.myshopify.com", "id", "secret")


def test_fetch_access_token_missing_access_token_raises():
    with patch("based_inventory.auth.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200,
            {"scope": "read_products", "expires_in": 86399},
        )

        with pytest.raises(RuntimeError, match="missing access_token"):
            fetch_access_token("store.myshopify.com", "id", "secret")
