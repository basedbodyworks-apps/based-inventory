"""Daily 7am Pacific (after quantity_alerts): scan ShipHero inventory_changes
for LARGE non-shipping events and post them to Slack.

Why this job exists:
A 2026-05-07 probe of curl cream (BB-CC-SINGLE) found two manual stock
adjustments totaling -5,774 units in a single week, with reason
'Change from the product page via the ShipHero Web Dashboard'. That kind
of event could mask a stock count error, theft, write-off, or transfer.
The existing quantity_alerts job correctly EXCLUDES non-shipping events
from velocity (right call), but as a result, big adjustments go silently.
This job surfaces them.

Scope:
- Scans inventory_changes WITH a sku filter for each tracked component SKU
  (~30 SKUs total — same candidate set as quantity_alerts).
- 24-hour lookback window.
- Flags any single event with abs(change_in_on_hand) >= ANOMALY_THRESHOLD
  AND the reason is NOT a normal "Order ... shipped" event.
- Groups by SKU + reason; posts one Slack block per anomaly.

Cost:
- 1 page (~100 credits) per SKU per run, ~30 SKUs = ~3000 credits. Well
  within the per-op cap of 4004 and the +60/min refill rate.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from based_inventory.config import Config
from based_inventory.discontinued import DiscontinuedFilter
from based_inventory.jobs._common import run_job
from based_inventory.registry import build_registry
from based_inventory.shiphero import MERCHDROP_WAREHOUSE_ID, ShipHeroClient
from based_inventory.shiphero_auth import resolve_access_token
from based_inventory.slack import SlackClient, context, divider, header, section

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
COMPONENTS_PATH = DATA_DIR / "set-components.json"
DISCONTINUED_PATH = DATA_DIR / "discontinued-skus.json"

# Lookback window for anomaly detection. Daily cadence so 24h is right.
ANOMALY_WINDOW_HOURS = 24

# Any single event with absolute change >= this is flagged. 500 units is
# big enough to filter out routine bundle assemblies but small enough to
# catch most stock-correction events. Tune via env if needed.
ANOMALY_THRESHOLD = 500

# Per-SKU page cap when paginating inventory_changes. 500 events / SKU / 24h
# is well above any observed rate; this is a runaway-cost guard.
MAX_PAGES_PER_SKU = 5

# Reasons we treat as "normal" (do not flag). Anything matching is suppressed
# regardless of size — these are expected high-volume events.
_NORMAL_REASON_PATTERNS = [
    re.compile(r"order.*shipped", re.IGNORECASE),
    re.compile(r"kit sku", re.IGNORECASE),  # kit-rollup events are normal
]


@dataclass
class Anomaly:
    sku: str
    product_name: str
    change_in_on_hand: int
    reason_short: str
    reason_full: str
    created_at: str


def _is_normal(reason: str) -> bool:
    return any(p.search(reason) for p in _NORMAL_REASON_PATTERNS)


def _summarize_reason(reason: str) -> str:
    """Strip HTML and collapse whitespace for display."""
    cleaned = re.sub(r"<[^>]+>", "", reason)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:120] + ("…" if len(cleaned) > 120 else "")


def _fetch_anomalies_for_sku(
    client: ShipHeroClient,
    sku: str,
    since_iso: str,
) -> list[tuple[int, str, str]]:
    """Return (change_in_on_hand, reason, created_at) for all anomalies in window."""
    query = """
    query($sku: String!, $warehouse_id: String!, $date_from: ISODateTime!) {
      inventory_changes(sku: $sku, warehouse_id: $warehouse_id, date_from: $date_from) {
        data {
          edges { node { id change_in_on_hand reason created_at } }
          pageInfo { hasNextPage }
        }
      }
    }
    """
    seen: set[str] = set()
    out: list[tuple[int, str, str]] = []
    cur = since_iso
    pages = 0
    while pages < MAX_PAGES_PER_SKU:
        pages += 1
        payload = client._execute(
            query,
            {"sku": sku, "warehouse_id": MERCHDROP_WAREHOUSE_ID, "date_from": cur},
        )
        edges = payload["data"]["inventory_changes"]["data"]["edges"]
        if not edges:
            break
        progress = False
        last = cur
        for e in edges:
            n = e["node"]
            eid = n.get("id") or ""
            if eid and eid in seen:
                continue
            if eid:
                seen.add(eid)
            progress = True
            created = n.get("created_at") or last
            if created > last:
                last = created
            change = int(n.get("change_in_on_hand") or 0)
            reason = (n.get("reason") or "").strip()
            if abs(change) < ANOMALY_THRESHOLD:
                continue
            if _is_normal(reason):
                continue
            out.append((change, reason, created))
        if not progress:
            break
        if not payload["data"]["inventory_changes"]["data"]["pageInfo"]["hasNextPage"]:
            break
        if last == cur:
            break
        cur = last
        time.sleep(0.2)
    return out


def build_blocks(anomalies: list[Anomaly]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        header("📊 Inventory Anomaly Alert"),
        section(
            f"Detected {len(anomalies)} non-shipping inventory event(s) "
            f"≥{ANOMALY_THRESHOLD:,} units in the last {ANOMALY_WINDOW_HOURS}h. "
            "These are NOT normal sales and may need investigation "
            "(stock count, transfer, write-off, or correction)."
        ),
        divider(),
    ]
    # Sort: largest absolute change first.
    # Note: U+2212 MINUS SIGN (rather than ASCII hyphen-minus) is intentional —
    # it renders as a wider, visually distinct minus in Slack body text where
    # ASCII '-' can be lost next to other punctuation. ruff RUF001 noqa.
    for a in sorted(anomalies, key=lambda x: -abs(x.change_in_on_hand)):
        sign = "−" if a.change_in_on_hand < 0 else "+"  # noqa: RUF001
        magnitude = f"{sign}{abs(a.change_in_on_hand):,}"
        ts = a.created_at[:19].replace("T", " ")
        blocks.append(
            section(
                f"*{a.product_name or a.sku}*  `{a.sku}`\n"
                f"📉  Change: *{magnitude}* units  ·  {ts} UTC\n"
                f"💬  _{a.reason_short}_"
            )
        )
    blocks.append(divider())
    blocks.append(
        context(
            f"🕐  daily anomaly scan  ·  threshold ≥{ANOMALY_THRESHOLD:,} units  ·  "
            f"window {ANOMALY_WINDOW_HOURS}h  ·  source: ShipHero (Merchdrop)"
        )
    )
    return blocks


def _run(cfg: Config) -> None:
    access_token = resolve_access_token(
        refresh_token=cfg.shiphero_refresh_token,
        fallback_access_token=cfg.shiphero_access_token,
    )
    client = ShipHeroClient(token=access_token, api_url=cfg.shiphero_api_url)

    discontinued = DiscontinuedFilter(DISCONTINUED_PATH)
    slack = SlackClient(cfg.slack_bot_token, cfg.slack_channel, dry_run=cfg.dry_run)

    stock = client.fetch_warehouse_stock(warehouse_id=MERCHDROP_WAREHOUSE_ID)
    kits = client.fetch_all_kits()

    # Same candidate scoping as quantity_alerts: real component SKUs only.
    component_skus = {c[0] for k in kits for c in k.components}
    known = {s.sku for s in stock}
    for sku in sorted(component_skus - known):
        try:
            row = client.fetch_warehouse_product_for_sku(sku, MERCHDROP_WAREHOUSE_ID)
            if row is not None:
                stock.append(row)
        except RuntimeError:
            continue

    registry = build_registry(kits, stock, COMPONENTS_PATH)
    candidates = [
        s
        for s in stock
        if not s.is_kit
        and s.sku not in registry.bundle_skus
        and s.sku in component_skus
        and not discontinued.should_skip(s.sku, s.product_name)
    ]

    since = time.strftime(
        "%Y-%m-%dT%H:%M:%S",
        time.gmtime(time.time() - ANOMALY_WINDOW_HOURS * 3600),
    )

    anomalies: list[Anomaly] = []
    for s in candidates:
        try:
            events = _fetch_anomalies_for_sku(client, s.sku, since)
        except RuntimeError:
            continue
        for change, reason, created in events:
            anomalies.append(
                Anomaly(
                    sku=s.sku,
                    product_name=s.product_name,
                    change_in_on_hand=change,
                    reason_short=_summarize_reason(reason),
                    reason_full=reason,
                    created_at=created,
                )
            )

    if not anomalies:
        return

    blocks = build_blocks(anomalies)
    fallback = f"📊 Inventory Anomaly Alert: {len(anomalies)} large non-shipping event(s)"
    slack.post_message(fallback, blocks)


def main() -> None:
    run_job("anomaly_alerts", _run)


if __name__ == "__main__":
    main()
