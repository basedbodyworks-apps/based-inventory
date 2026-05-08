"""Amazon Selling Partner API (SP-API) client.

Why this exists:
A 2026-05-08 ShipHero probe found that ShipHero's `Product.fba_inventory`
exposes only 5 fields (id, legacy_id, quantity, marketplace_id, merchant_id)
and only ~2 of 11 Based hero SKUs returned any FBA rows at all. Amazon
SP-API gives the full picture: fulfillable, inbound, reserved, unsellable,
researching, plus per-SKU/per-ASIN granularity across every FBA listing.

Endpoint host:
- North America (US, Canada, Mexico, Brazil): https://sellingpartnerapi-na.amazon.com
- Europe: https://sellingpartnerapi-eu.amazon.com
- Far East: https://sellingpartnerapi-fe.amazon.com

Based is US-only at the moment (marketplace ATVPDKIKX0DER), so we hardcode NA.

Auth:
- LWA refresh token → short-lived access token (1h) via amazon_auth.py.
- Access token passed as `x-amz-access-token` header on every request.
- No AWS Sigv4 signing required since 2024 (Amazon dropped that).

Rate limits (per docs as of 2026-05):
- getInventorySummaries: 2 req/sec, burst 2.
- We sleep 0.6s between calls as a safe single-thread pacer.

Pagination:
- SP-API uses `nextToken` cursor in the response payload. Pass back as
  `nextToken` query param to get the next page.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

NA_HOST = "https://sellingpartnerapi-na.amazon.com"
US_MARKETPLACE_ID = "ATVPDKIKX0DER"

# Conservative inter-call pacing for single-thread use. SP-API rate limits
# are per-app per-endpoint; getInventorySummaries is documented at 2 req/sec.
_DEFAULT_PACE_SECONDS = 0.6


@dataclass(frozen=True)
class FbaInventorySummary:
    """Normalized FBA inventory row for one SKU/ASIN/marketplace tuple.

    Mirrors the SP-API InventorySummary shape with the fields we actually
    use. Raw response is preserved on `raw` for debugging if needed.
    """

    seller_sku: str
    asin: str
    fn_sku: str | None  # Amazon's internal fulfillment SKU
    product_name: str
    condition: str
    last_updated_time: str | None
    # Aggregate quantities. ShipHero only gives `quantity`; SP-API splits these out.
    fulfillable: int
    inbound_working: int
    inbound_shipped: int
    inbound_receiving: int
    reserved_total: int
    researching: int
    unsellable_total: int
    raw: dict[str, Any]


class AmazonSPClient:
    def __init__(
        self,
        access_token: str,
        marketplace_id: str = US_MARKETPLACE_ID,
        host: str = NA_HOST,
        pace_seconds: float = _DEFAULT_PACE_SECONDS,
    ) -> None:
        self.access_token = access_token
        self.marketplace_id = marketplace_id
        self.host = host
        self.pace_seconds = pace_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "x-amz-access-token": self.access_token,
            "Accept": "application/json",
            "User-Agent": "based-inventory/0.1 (Language=Python)",
        }

    def _get(
        self, path: str, params: dict[str, Any] | None = None, retries: int = 4
    ) -> dict[str, Any]:
        url = f"{self.host}{path}"
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = requests.get(url, params=params, headers=self._headers(), timeout=60)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                time.sleep(min(2**attempt, 10))
                continue
            if r.status_code == 429:
                # Rate-limited. Honor the rate-limit header if present.
                retry_after = r.headers.get("x-amzn-RateLimit-Limit")
                wait = float(retry_after) if retry_after else min(2**attempt, 10)
                logger.warning("SP-API 429 on %s; waiting %.1fs", path, wait)
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                logger.warning("SP-API %d on %s; retrying", r.status_code, path)
                time.sleep(min(2**attempt, 10))
                continue
            if r.status_code != 200:
                raise RuntimeError(f"SP-API {path} failed HTTP {r.status_code}: {r.text[:500]}")
            try:
                return r.json()
            except ValueError as exc:
                raise RuntimeError(f"SP-API {path} returned non-JSON body") from exc
        raise RuntimeError(f"SP-API {path} exhausted retries: {last_exc}")

    # -----------------------------------------------------------------------
    # FBA Inventory: full breakdown per SKU/ASIN
    # -----------------------------------------------------------------------
    def fetch_fba_inventory_summaries(
        self,
        seller_skus: list[str] | None = None,
        granularity_type: str = "Marketplace",
        details: bool = True,
    ) -> list[FbaInventorySummary]:
        """Pull FBA inventory for the seller's catalog, optionally filtered to specific SKUs.

        Args:
            seller_skus: optional list of seller SKUs to filter to. Capped at 50
                per Amazon's API limit; the caller is responsible for chunking.
                When None, returns ALL inventory in the marketplace (paginated).
            granularity_type: 'Marketplace' is the default and recommended.
            details: when True, response includes the inventoryDetails object
                (fulfillable, inbound, reserved, unsellable). When False, only
                totalQuantity is returned.

        Returns: list of FbaInventorySummary across all pages.
        """
        if seller_skus and len(seller_skus) > 50:
            raise ValueError("seller_skus list is capped at 50 per SP-API call")

        out: list[FbaInventorySummary] = []
        next_token: str | None = None
        page = 0
        while True:
            page += 1
            params: dict[str, Any] = {
                "granularityType": granularity_type,
                "granularityId": self.marketplace_id,
                "marketplaceIds": self.marketplace_id,
                "details": "true" if details else "false",
            }
            if seller_skus:
                params["sellerSkus"] = ",".join(seller_skus)
            if next_token:
                params["nextToken"] = next_token

            payload = self._get("/fba/inventory/v1/summaries", params=params)
            data = payload.get("payload") or {}
            summaries = data.get("inventorySummaries") or []
            for s in summaries:
                out.append(_parse_summary(s))

            next_token = data.get("nextToken")
            if not next_token:
                break
            # Pace before the next page to respect rate limit (2 req/sec).
            time.sleep(self.pace_seconds)
        return out


def _parse_summary(raw: dict[str, Any]) -> FbaInventorySummary:
    details = raw.get("inventoryDetails") or {}
    inbound = details.get("inboundShippedQuantity")
    inbound_working = details.get("inboundWorkingQuantity")
    inbound_receiving = details.get("inboundReceivingQuantity")
    reserved = details.get("reservedQuantity") or {}
    researching = details.get("researchingQuantity") or {}
    unsellable = details.get("unfulfillableQuantity") or {}
    return FbaInventorySummary(
        seller_sku=raw.get("sellerSku") or "",
        asin=raw.get("asin") or "",
        fn_sku=raw.get("fnSku"),
        product_name=raw.get("productName") or "",
        condition=raw.get("condition") or "",
        last_updated_time=raw.get("lastUpdatedTime"),
        fulfillable=int(details.get("fulfillableQuantity") or 0),
        inbound_working=int(inbound_working or 0),
        inbound_shipped=int(inbound or 0),
        inbound_receiving=int(inbound_receiving or 0),
        reserved_total=int(reserved.get("totalReservedQuantity") or 0),
        researching=int(researching.get("totalResearchingQuantity") or 0),
        unsellable_total=int(unsellable.get("totalUnfulfillableQuantity") or 0),
        raw=raw,
    )
