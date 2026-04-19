"""Shopify Admin GraphQL client."""

from __future__ import annotations

import time
from typing import Any

import requests

_PRODUCTS_QUERY = """
query($cursor: String) {
  products(first: 50, query: "status:active", after: $cursor) {
    edges {
      cursor
      node {
        id
        title
        handle
        totalInventory
        variants(first: 100) {
          edges {
            node {
              id
              title
              sku
              inventoryQuantity
              inventoryPolicy
              inventoryItem {
                tracked
                inventoryLevels(first: 10) {
                  edges {
                    node {
                      quantities(names: ["available"]) {
                        name
                        quantity
                      }
                      location {
                        id
                        name
                        shipsInventory
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage }
  }
}
"""


class ShopifyClient:
    def __init__(self, store: str, token: str, api_version: str = "2026-01") -> None:
        self.endpoint = f"https://{store}/admin/api/{api_version}/graphql.json"
        self.headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        }

    def _execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.post(
            self.endpoint,
            json={"query": query, "variables": variables or {}},
            headers=self.headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if "errors" in payload:
            raise RuntimeError(f"Shopify GraphQL errors: {payload['errors']}")
        return payload

    def fetch_all_products(self) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload = self._execute(_PRODUCTS_QUERY, {"cursor": cursor})
            data = payload["data"]["products"]
            for edge in data["edges"]:
                node = edge["node"]
                products.append(
                    {
                        "id": node["id"],
                        "title": node["title"],
                        "handle": node["handle"],
                        "totalInventory": node["totalInventory"],
                        "variants": [
                            self._flatten_variant(v["node"]) for v in node["variants"]["edges"]
                        ],
                    }
                )
                cursor = edge["cursor"]
            if not data["pageInfo"]["hasNextPage"]:
                break
            time.sleep(1)
        return products

    @staticmethod
    def _flatten_variant(node: dict[str, Any]) -> dict[str, Any]:
        item = node.get("inventoryItem") or {}
        levels_container = item.get("inventoryLevels") or {}
        levels = []
        for lvl in levels_container.get("edges", []):
            lnode = lvl["node"]
            available = next(
                (
                    q["quantity"]
                    for q in lnode.get("quantities", [])
                    if q.get("name") == "available"
                ),
                0,
            )
            levels.append({"available": available, "location": lnode["location"]})
        return {
            "id": node["id"],
            "title": node["title"],
            "sku": node.get("sku"),
            "inventoryQuantity": node.get("inventoryQuantity", 0),
            "inventoryPolicy": node.get("inventoryPolicy", "DENY"),
            "inventoryItem": {
                "tracked": item.get("tracked", True),
                "inventoryLevels": levels,
            },
        }
