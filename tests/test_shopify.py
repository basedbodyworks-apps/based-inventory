"""Tests for Shopify GraphQL client."""

from unittest.mock import MagicMock

from based_inventory.shopify import ShopifyClient


def test_fetch_all_products_paginates(monkeypatch):
    responses = [
        {
            "data": {
                "products": {
                    "edges": [
                        {
                            "cursor": "c1",
                            "node": {
                                "id": "gid://shopify/Product/1",
                                "title": "Shampoo",
                                "handle": "shampoo",
                                "totalInventory": 100,
                                "variants": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "gid://shopify/ProductVariant/11",
                                                "title": "Just One",
                                                "sku": "S-1",
                                                "inventoryQuantity": 100,
                                                "inventoryPolicy": "DENY",
                                                "inventoryItem": {
                                                    "tracked": True,
                                                    "inventoryLevels": {
                                                        "edges": [
                                                            {
                                                                "node": {
                                                                    "available": 100,
                                                                    "location": {
                                                                        "id": "L1",
                                                                        "name": "TX",
                                                                        "shipsInventory": True,
                                                                    },
                                                                }
                                                            }
                                                        ]
                                                    },
                                                },
                                            }
                                        }
                                    ]
                                },
                            },
                        }
                    ],
                    "pageInfo": {"hasNextPage": True},
                }
            }
        },
        {
            "data": {
                "products": {
                    "edges": [
                        {
                            "cursor": "c2",
                            "node": {
                                "id": "gid://shopify/Product/2",
                                "title": "Conditioner",
                                "handle": "conditioner",
                                "totalInventory": 50,
                                "variants": {"edges": []},
                            },
                        }
                    ],
                    "pageInfo": {"hasNextPage": False},
                }
            }
        },
    ]

    mock_post = MagicMock(side_effect=[_mock_response(r) for r in responses])
    monkeypatch.setattr("based_inventory.shopify.requests.post", mock_post)
    monkeypatch.setattr("based_inventory.shopify.time.sleep", lambda _: None)

    client = ShopifyClient(store="test.myshopify.com", token="shpat_test", api_version="2026-01")
    products = client.fetch_all_products()

    assert len(products) == 2
    assert products[0]["title"] == "Shampoo"
    assert products[0]["handle"] == "shampoo"
    assert products[0]["variants"][0]["title"] == "Just One"
    assert products[0]["variants"][0]["inventoryPolicy"] == "DENY"
    assert products[1]["title"] == "Conditioner"
    assert mock_post.call_count == 2


def _mock_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response
