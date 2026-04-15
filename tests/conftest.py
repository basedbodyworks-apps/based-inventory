"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def fake_product():
    """Minimal product dict matching the Shopify GraphQL shape."""
    return {
        "id": "gid://shopify/Product/1",
        "title": "Shampoo",
        "handle": "shampoo",
        "totalInventory": 5000,
        "variants": [
            {
                "id": "gid://shopify/ProductVariant/11",
                "title": "Just One",
                "sku": "SHAMPOO-SINGLE",
                "inventoryQuantity": 3000,
                "inventoryPolicy": "DENY",
                "inventoryItem": {
                    "tracked": True,
                    "inventoryLevels": [
                        {
                            "available": 3000,
                            "location": {"id": "loc1", "name": "TX", "shipsInventory": True},
                        },
                    ],
                },
            },
            {
                "id": "gid://shopify/ProductVariant/12",
                "title": "Two Pack",
                "sku": "SHAMPOO-2PACK",
                "inventoryQuantity": 200,
                "inventoryPolicy": "DENY",
                "inventoryItem": {
                    "tracked": True,
                    "inventoryLevels": [
                        {
                            "available": 200,
                            "location": {"id": "loc1", "name": "TX", "shipsInventory": True},
                        },
                    ],
                },
            },
        ],
    }
