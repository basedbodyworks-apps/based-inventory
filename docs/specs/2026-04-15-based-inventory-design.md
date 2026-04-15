# Based Inventory — Design Spec

**Author:** Avi Jhingan, Director of eCommerce, Based BodyWorks
**Date:** 2026-04-15
**Status:** Design, pre-implementation
**Repo:** [basedbodyworks-apps/based-inventory](https://github.com/basedbodyworks-apps/based-inventory)

---

## 1. Goal

One bot that owns inventory truth in Slack `#alerts-inventory` for Based BodyWorks. Replaces the existing "Inventory Brain" (runs on a colleague's laptop, Python stdlib only) and adds a new site-crawl audit layer that reconciles Shopify inventory truth against rendered Add-to-Cart (ATC) button state on the live site.

Three things this bot does, every day, unattended:

1. **Quantity alerts.** Catch singles-variant inventory dropping below severity tiers (1000 / 750 / 500 / 100) and post escalating alerts with the right @-mentions. Dedupe via persistent state so we only re-alert on crossings into a lower tier.
2. **ATC audit.** Enumerate every URL each product appears on (PDPs, collections, Instant Commerce landing pages), render each page with Playwright, read the ATC button state per variant, and compare against Shopify inventory truth. Flag three classes of disagreement: SALES LEAK (inventory > 0 but ATC missing/disabled), OVERSELL RISK (inventory ≤ 0 but ATC clickable), NO BUY BUTTON (product rendered on a page with no ATC element at all).
3. **Weekly snapshot.** Fridays 9am PST Block Kit post of all tracked products, grouped by category, with scent breakdowns and bottleneck-set annotations. Same format as the existing `weekly_audit.py`.

---

## 2. Scope

**In scope for v0:**
- Shopify Admin GraphQL as source of truth for inventory
- All active Shopify products minus a hardcoded skip list
- PDP URLs (`/products/{handle}`), collection URLs (`/collections/{handle}`), and Instant Commerce landing pages at `/pages/*` discovered via `sitemap.xml`
- Playwright-driven ATC state detection, variant-aware
- Slack Block Kit alerts to `#alerts-inventory` (channel ID `C0AK6UGA1NJ`) under a new bot identity "Based Inventory"
- Telegram fallback on Slack delivery failure
- Persistent state file for alert dedup
- Deployed on Render as three separate cron services sharing one Docker image

**Out of scope for v0, deferred to v1+:**
- ShipHero API integration for three-way reconciliation (Shopify ↔ ShipHero ↔ site)
- Screenshot artifacts and a Next.js dashboard
- Historical trend analysis
- Linear ticket automation on flag creation
- Auto-hide via `publishedScope` toggle
- Fast-path static HTML fetch optimization for server-rendered placements

**Explicit non-goals:**
- Not a marketing analytics tool
- Not a pricing audit tool
- Not a general "site health" tool; scope is bounded to inventory truth vs rendered ATC state

---

## 3. Architecture

### 3.1 Repository layout

```
based-inventory/
├── Dockerfile                    # Python 3.12 + playwright + chromium
├── requirements.txt              # requests, playwright
├── pyproject.toml                # lint/format config
├── README.md                     # runbook, env vars, local dev
├── .env.example                  # template for env vars
├── .github/workflows/            # CI: lint, type-check, unit tests
├── docs/
│   └── specs/
│       └── 2026-04-15-based-inventory-design.md
├── src/
│   └── based_inventory/
│       ├── __init__.py
│       ├── config.py             # env var loading, constants
│       ├── shopify.py            # GraphQL client, product/variant models
│       ├── singles.py            # singles-variant resolver + multi-scent summing
│       ├── sets.py               # virtual-bundle resolution, set components loader
│       ├── skip_list.py          # hardcoded exclusion list
│       ├── slack.py              # Block Kit helpers, chat.postMessage
│       ├── telegram.py           # openclaw fallback
│       ├── state.py              # alert-state.json read/write + extended schema
│       ├── crawl/
│       │   ├── __init__.py
│       │   ├── urls.py           # sitemap, products.json, collections.json enumeration
│       │   ├── atc.py            # Playwright-driven ATC state detection
│       │   └── selectors.py      # ATC selector strategy with fallbacks
│       ├── jobs/
│       │   ├── __init__.py
│       │   ├── quantity_alerts.py   # 6h cron entrypoint
│       │   ├── atc_audit.py         # daily cron entrypoint
│       │   └── weekly_snapshot.py   # Fridays 9am PST entrypoint
│       └── logging.py            # structured logging to stdout (Render captures)
├── data/
│   ├── set-components.json       # copied verbatim from Inventory Brain
│   └── alert-state.json          # gitignored; persisted on Render disk or S3
├── tests/
│   ├── fixtures/                 # saved HTML snapshots of known-good / known-bad ATC states
│   ├── test_singles.py
│   ├── test_sets.py
│   ├── test_atc_detection.py
│   └── test_state.py
└── scripts/
    ├── backfill-state.py         # seed alert-state.json from current Shopify truth
    └── manual-run.sh             # dry-run wrapper for local debugging
```

### 3.2 Three cron jobs, one codebase

| Job | Schedule | Runtime | Purpose |
|---|---|---|---|
| `quantity_alerts` | every 6h | ~30s | Shopify GraphQL pull, evaluate tiers, dedupe against state, post Block Kit alert |
| `atc_audit` | daily 6am PST | ~10-15 min | Shopify pull + site crawl + diff + Block Kit alert |
| `weekly_snapshot` | Fridays 9am PST | ~45s | Shopify pull, Block Kit full snapshot |

All three share the same `src/based_inventory/` modules. Entrypoints live in `jobs/`. Render runs each as a separate cron service pointing at the same Docker image with a different `CMD`.

### 3.3 Language and dependencies

Python 3.12. Dependencies:
- `requests` (HTTP, Shopify GraphQL, Slack API)
- `playwright` (Python bindings, chromium only)
- `python-dotenv` (local dev)

No ORM, no web framework, no heavy libraries. Module boundaries are clean enough that unit tests can mock the Shopify and Playwright layers independently.

---

## 4. Inventory model (must match Inventory Brain)

### 4.1 Singles-only rule

Every Based product has multiple variants: "Just One", "Two Pack", "Three Pack", multi-scent bundles. Only the single-unit variant controls reality. Multi-pack variants have their own Shopify `inventoryQuantity` that is meaningless because they pull from singles at fulfillment time.

Single-variant detection (ported from `check_inventory.py:190-234`):

1. If variant title contains "just" or "single" → single
2. If variant title is "default title" or "full size" → standalone product, use as-is
3. If SKU contains "single" → single
4. If multiple matches → multi-scent product, sum across scents

**Implication for ATC audit:** for a 2-pack variant, expected sellable is derived from `single_qty ÷ 2`, not the 2-pack's own `inventoryQuantity`. For a 3-pack, `single_qty ÷ 3`. Naive variant-level checks produce false negatives (2-pack shows ATC, Shopify says qty 50, but singles are at 0: correct state is OVERSELL RISK, naive audit says OK).

### 4.2 Sets are virtual bundles

The 23 sets in `data/set-components.json` (Shower Duo, Curly Duo, Complete Styling Kit, etc.) have no own inventory. Set capacity = `min(component_single_qty)`. Sets are real Shopify products with real PDPs, so the ATC audit will crawl them.

Expected-sellable logic for a set product: `min(component_single_qty) > 0`. Component at 0 with set PDP showing ATC enabled is OVERSELL RISK. Attribution on the alert includes the blocking component.

`set-components.json` is copied verbatim from Inventory Brain; no rebuild.

### 4.3 Multi-scent handling diverges between jobs

- **Quantity alerts:** sum across scents for a product-level total (Body Wash, Deodorant).
- **ATC audit:** iterate each (scent × pack-size) combination independently. Each scent is an addressable variant on the PDP picker with its own ATC state. Summing here would hide real leaks.

### 4.4 Skip list

Hardcoded in `src/based_inventory/skip_list.py`. Merged from `check_inventory.py`'s `SKIP_TITLES` and `INVENTORY-RULES.md`:

- Shipping Protection (all variants)
- Based Membership
- Brand Ambassador Package
- Based Wooden Comb - Light (legacy)
- Shipping, Shipping International
- 4oz Shampoo + Conditioner Bundle Sample
- Bath Stone (White)
- T-Shirts
- Non-fulfillment test products
- Legacy: Based Conditioner 1.0, Based Conditioner 2.0, Based Shampoo 1.0, Based Shampoo 2.0
- Legacy: Hair Revival Serum, Super Serum, Showerhead Filter, Based Wooden Comb (original)
- Any product with `status: archived` (filtered at GraphQL layer via `query: "status:active"`)

Skip list applies to all three jobs.

### 4.5 Daily Facial Cleanser / Moisturizer edge case

Real SKUs with real inventory, but no standalone PDPs. They exist only as variants of "Daily Skincare Duo". When rolling up set-component inventory for Full Skincare Kit, Revitalizing Trio, Refreshing Trio, these two resolve via the Daily Skincare Duo variants rather than a standalone product lookup. Logic ports from `weekly_audit.py:157-160`.

For the ATC audit: skip the "crawl every URL" step for these two, since they have no public-facing PDP. They still appear in quantity alerts and weekly snapshot.

### 4.6 Negative inventory (oversold)

- **Quantity alerts:** skip `single_qty < 0`. There's no useful tier alert past zero (Inventory Brain's current behavior).
- **ATC audit:** do NOT skip. Oversold inventory with an enabled ATC is literally the definition of OVERSELL RISK and is the point of the audit.
- **Weekly snapshot:** render `⛔` for oversold variants (current behavior).

---

## 5. ATC audit flow (new)

### 5.1 URL enumeration

Three sources, unioned:

1. **PDPs.** `GET https://basedbodyworks.com/products.json?limit=250&page=N` walks the full product catalog. PDP URL = `/products/{handle}`. Public, no auth.
2. **Collections.** `GET /collections.json` + `/collections/{handle}/products.json` gives collection-card placement. Collection URL = `/collections/{handle}`.
3. **Instant Commerce landing pages.** `GET /sitemap.xml`, follow sitemap-index links, filter entries under `/pages/*`. This is the v0 approach per Avi's decision: it will miss any page not yet in the sitemap (typically a 24-48h lag for newly published Instant pages). v1 can swap to an Instant admin API pull if one becomes available, or a marketing-maintained YAML list.

### 5.2 Page render

Playwright, chromium, `playwright-python`. One browser instance, 2-4 concurrent contexts. Per page:

1. `page.goto(url, wait_until="networkidle")`
2. Wait up to 10s for a known ATC-containing selector. If selector never appears, record `no_atc_element` and move on.
3. For PDPs: enumerate the variant picker. For each variant option, click (or programmatically select) and re-read the ATC button.
4. For collection pages and landing pages: each product card has its own quick-add or links to the PDP. Read whatever ATC-shaped element is present per card.
5. Capture per location per variant: `present` (bool), `enabled` (not `disabled`, not `aria-disabled="true"`, classes don't include sold-out patterns), `text` (button copy: "Add to cart" / "Sold out" / "Notify me" / "Coming soon").

### 5.3 Selector strategy

Multiple fallbacks, ordered, in `src/based_inventory/crawl/selectors.py`:

1. `[name="add"]`
2. `button[type="submit"][form*="product-form"]`
3. `[data-testid*="add-to-cart" i]`
4. Instant Commerce-specific selectors (pinned after one-time DOM inspection of a representative Instant page; expected to include a data attribute on Instant's product-block components)
5. Text-regex fallback: button whose innerText matches `/add to cart|sold out|notify me|coming soon/i`

Any selector hit short-circuits the rest. Config lives in code (not JSON) so selector changes go through PR review.

### 5.4 Bot detection mitigation

Shopify storefronts front Cloudflare. Playwright with no stealth measures can trip WAF challenges, especially from datacenter IPs.

- `playwright-extra` + stealth plugin (or equivalent Python port)
- Real desktop User-Agent string
- Request rate limited to 2-4 concurrent pages
- Random 500-1500ms delays between page loads
- Respect `robots.txt` for the `/pages/*` crawl

Render runs from known datacenter IPs. If Cloudflare challenges persist, v0 fallback is a residential-IP proxy (Bright Data, Oxylabs) for the crawl-only job. Quantity alerts and weekly snapshot hit the Shopify Admin API directly and are not subject to this.

### 5.5 Expected vs observed diff

For each `(variant, location_url)`:

1. Compute `expected_state`:
   - If `inventoryPolicy == "continue"`: `expected = sellable` (backorder allowed; ATC enabled is correct regardless of qty)
   - Else if variant is a single: `expected = sellable if single_qty > 0 else oos`
   - Else if variant is a 2-pack or 3-pack: `expected = sellable if (single_qty ÷ pack_size) >= 1 else oos`
   - Else if product is a set: `expected = sellable if min(component_single_qty) > 0 else oos`
2. Read `observed_state` from Playwright: `sellable` if ATC is present and enabled with "add to cart" text; `oos` if "sold out" / "notify me" / disabled; `missing` if no ATC element at all.
3. Compare:
   - `expected=sellable, observed=oos` → **SALES LEAK**
   - `expected=oos, observed=sellable` → **OVERSELL RISK**
   - `observed=missing` → **NO BUY BUTTON** (regardless of expected)
   - match → no alert

---

## 6. Alert schema

### 6.1 Severity ladder (quantity alerts, ported)

| Singles ≤ | Icon | Label | @-mentions |
|---|---|---|---|
| 1,000 | 🟡 | HEADS UP | (none) |
| 750 | 🟠 | WARNING | Carlos |
| 500 | 🔴 | LOW STOCK | Carlos |
| 100 | 🚨 | CRITICAL | Carlos + Ryan + Alex |

All tiers also tag `@channel`. Re-alert only fires on crossings into a lower tier than previously recorded state.

User IDs (confirmed against Inventory Brain source):
- Carlos: `U08LCCS7V5Z`
- Ryan: `U0AHP969HC5`
- Alex: `U09GXTYGT44`

### 6.2 Severity ladder (weekly snapshot, ported)

| Singles | Icon |
|---|---|
| 5,000+ | 🟢 |
| 1,000-5,000 | 📊 |
| ≤ 1,000 | 🟡 |
| ≤ 750 | 🟠 |
| ≤ 500 | 🔴 |
| ≤ 100 | 🚨 |
| Oversold | ⛔ |

### 6.3 ATC flag types (new)

| Flag | Icon | Meaning | @-mentions |
|---|---|---|---|
| SALES LEAK | 🛒 | inventory > 0 but ATC missing or disabled | Alex + Ryan |
| OVERSELL RISK | ⚠️ | inventory ≤ 0 but ATC clickable (and `inventoryPolicy == deny`) | Carlos |
| NO BUY BUTTON | 👻 | product rendered on URL with no ATC element at all | Alex + Ryan |

All tiers also tag `@channel`. Mention routing rationale: Carlos owns ops/fulfillment (oversell risk is a fulfillment-pain alert); Alex and Ryan own site/ecom (SALES LEAK and NO BUY BUTTON are site-config issues).

**v0 required footer on every OVERSELL RISK alert:**
> ⚠️ v0 limitation: this alert trusts Shopify `inventoryQuantity` as source of truth. Shopify and ShipHero can drift when toggles are changed in one system without the other. Verify against ShipHero before action. v1 will reconcile automatically.

### 6.4 Block Kit format

Follow the existing `check_inventory.py:313-349` pattern for quantity alerts and `weekly_audit.py:208-218` pattern for weekly snapshot. For ATC alerts, new shape:

```
⚡ ATC Audit — 3 disagreements found

🛒 SALES LEAK — *Curl Cream / Two Pack*
   📦 Shopify: 2,400 singles → 1,200 two-packs available
   🔗 /products/curl-cream (Santal Sandalwood variant)
   💬 ATC reads "Sold out" but inventory is healthy

⚠️ OVERSELL RISK — *Shower Duo*
   📦 Shopify: Shampoo at 0 singles (bottleneck)
   🔗 /products/shower-duo
   💬 ATC reads "Add to cart" but set cannot fulfill
   ⚠️ v0 limitation: verify against ShipHero before action

👻 NO BUY BUTTON — *Leave-In Conditioner*
   🔗 /pages/spring-hair-launch
   💬 Product block rendered but no ATC element found

🕐 Apr 15, 6:00 AM PST
<@U09GXTYGT44> <@U0AHP969HC5> <@U08LCCS7V5Z>
<!channel>
```

---

## 7. State and dedup

File: `data/alert-state.json`, persisted on Render disk (cheapest option) or S3 (more durable if we run multi-region later). Same schema as Inventory Brain's current state file, extended with a new `atc_flags` key:

```json
{
  "quantity_tiers": {
    "Shampoo": 500,
    "Conditioner": 750
  },
  "atc_flags": {
    "gid://shopify/ProductVariant/123::/products/curl-cream::SALES_LEAK": {
      "first_seen_at": "2026-04-15T06:03:12Z",
      "last_seen_at": "2026-04-15T06:03:12Z"
    }
  }
}
```

**Dedup rules:**

- **Quantity alerts:** post only when a product crosses into a lower tier than its last recorded tier. If it recovers (qty climbs back), clear the tier. If it gets worse, update and re-post.
- **ATC flags:** post on first occurrence. Do not re-post while the flag persists. Post a "RESOLVED" follow-up when the flag disappears (optional for v0, required for v1).
- **Key shape:** `{variant_gid}::{location_url}::{flag_type}`. Page-builder URLs normalized (strip query params, trailing slash).

Corruption recovery: if the file is missing or invalid JSON, reset to `{}` and log a warning. Next run will fully re-alert (acceptable; better than silently losing state).

---

## 8. Shopify integration

### 8.1 App posture

New Shopify app in the Based Partners org ([partners.shopify.com/4860762](https://partners.shopify.com/4860762/)). App name: "Based Inventory". Scopes:

- `read_products`
- `read_inventory`
- `read_locations`

Install flow:

1. Partners dashboard → Apps → Create app → "Based Inventory"
2. Configure Admin API scopes above
3. Generate a custom install link
4. Install on the dev store first; save token as `SHOPIFY_TOKEN_DEV`
5. Install on prod (`basedbodyworks.com`) when crawler is verified on dev; save as `SHOPIFY_TOKEN_PROD`
6. Render cron services read store domain + token from env vars

### 8.2 GraphQL queries

Pin API version `2026-01` (matches Inventory Brain). One primary query for all jobs:

```graphql
{
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
                      available
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
```

Note the additions versus Inventory Brain's current query:
- `handle` (needed for URL construction)
- `variants[].id` (needed for dedup state keys)
- `inventoryPolicy` (needed for OVERSELL RISK false-positive filtering)
- `inventoryItem.tracked` + `inventoryLevels` (needed for per-location availability; "sellable" = available at any `shipsInventory: true` location)

### 8.3 "Sellable" definition

v0: `sellable = any(level.available > 0 for level in levels where level.location.shipsInventory)`.

Inventory Brain's current logic only looks at `inventoryQuantity` (sum across locations), which collapses location nuance. New bot does it properly: a variant with 10 units at a non-shipping location is not sellable. This is a small correctness improvement over Inventory Brain, worth making at cutover.

### 8.4 Rate limits

Shopify GraphQL uses a cost-based bucket: 1,000 points, refilling 50/sec. The query above is ~15 cost units per page. At 50 products/page and 100 variants/product, a full pull is 40-80 pages, ~1200 cost units total, taking ~25-30 seconds. Sleep 1s between pages to stay under the bucket. Headroom is ample.

---

## 9. Slack integration

### 9.1 Bot app posture

New Slack app in the Based workspace, display name "Based Inventory". Scopes:
- `chat:write`
- `chat:write.public` (post to channels bot isn't a member of, just in case)

Channel: `#alerts-inventory` (ID `C0AK6UGA1NJ`, unchanged).

Installation requires a Slack workspace admin to approve. Avi is the requestor.

### 9.2 Posting pattern

`chat.postMessage` with both `text` (fallback for mobile notifications) and `blocks` (rich rendering). `unfurl_links: false` to keep posts compact.

On HTTP failure or `ok: false`, invoke Telegram fallback and log the Slack error.

### 9.3 Timestamp rendering

PST computed as `UTC - 7h` (Inventory Brain's current approach; no DST handling). Acceptable for v0; v1 uses `zoneinfo` and handles DST properly.

---

## 10. Error handling and observability

### 10.1 Failure routing

Inherited from Inventory Brain:

- Any unhandled exception in a job's `main()` → caught at top level, formatted as Telegram message, re-raised for Render to log
- Slack API failure inside `slack_post()` → Telegram fallback with the Slack error detail
- Telegram fallback uses `openclaw message send --channel telegram --to {chat_id} --message {msg}` via subprocess

If Telegram is also down: exception is still logged to stdout and Render alerts on non-zero exit. Two-tier fallback is enough for v0.

### 10.2 Logging

Structured JSON to stdout (Render captures automatically). Fields per line:
- `ts` (ISO 8601 UTC)
- `job` (`quantity_alerts` / `atc_audit` / `weekly_snapshot`)
- `level` (debug/info/warn/error)
- `event` (enum of known event names)
- `payload` (event-specific details)

Sensitive values (tokens, customer data) never logged.

### 10.3 Dry-run mode

Every job supports `--dry-run` flag (via env var `DRY_RUN=1`) that skips the Slack/Telegram post and prints the intended message to stdout instead. Used for local development and for the cutover parallel-run period.

---

## 11. Deployment

### 11.1 Render Cron services

Three separate Render cron services, all reading from the same Docker image built from this repo:

| Service | CMD | Schedule (cron) |
|---|---|---|
| `based-inventory-quantity` | `python -m based_inventory.jobs.quantity_alerts` | `0 */6 * * *` |
| `based-inventory-atc` | `python -m based_inventory.jobs.atc_audit` | `0 13 * * *` (6am PST assuming PST = UTC-7) |
| `based-inventory-weekly` | `python -m based_inventory.jobs.weekly_snapshot` | `0 16 * * 5` (9am PST Fridays) |

Plan tier: Starter ($7/mo per service). Total: $21/mo.

### 11.2 Env vars (set on each Render service)

```
SHOPIFY_STORE=basedbodyworks.myshopify.com
SHOPIFY_TOKEN=shpat_...
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=C0AK6UGA1NJ
TELEGRAM_CHAT_ID=...          # optional, leave empty to skip fallback
DRY_RUN=0                     # set to 1 during cutover parallel-run
ENV=prod                      # prod|dev, controls which Shopify store
```

### 11.3 Docker image

Base: `mcr.microsoft.com/playwright/python:v1.44.0-focal` (Playwright's official Python image with chromium and all dependencies preinstalled). Avoids the "install playwright, install chromium, install OS libs" dance.

### 11.4 Persistent state

`data/alert-state.json` persisted via Render Disk (10GB SSD, $1/mo) mounted at `/data`. Env var `STATE_PATH=/data/alert-state.json`.

Alternative: S3/R2 with object locking. Deferred to v1 unless multi-service write contention appears (unlikely at this cadence).

### 11.5 CI

GitHub Actions on PR:
- `ruff check` (lint)
- `ruff format --check` (format)
- `pyright` or `mypy` (type check)
- `pytest tests/` (unit tests)

No CD step; Render auto-deploys on `main` merge.

---

## 12. Testing

### 12.1 Unit tests

- `test_singles.py`: singles-variant detection across all known patterns (Just One, Full Size, Default Title, SKU-contains-single, multi-scent summing).
- `test_sets.py`: virtual-bundle resolution, min-component logic, Daily Skincare Duo edge case.
- `test_state.py`: tier crossings, state persistence, corruption recovery.
- `test_atc_detection.py`: ATC state parsing against saved HTML fixtures (known-good PDP, known-OOS PDP, known-missing-button landing page, Instant Commerce section).

### 12.2 Fixtures

`tests/fixtures/` contains saved HTML snapshots of representative pages. Fetched once, committed, never re-fetched unless the site layout changes. Each fixture is annotated with expected parse output.

### 12.3 Integration smoke test

`scripts/manual-run.sh` runs all three jobs end-to-end against the dev store with `DRY_RUN=1`. Runs in local Docker, matches production exactly. Used before every deploy.

---

## 13. Known risks

### 13.1 Top risk: Shopify/ShipHero divergence (flagged)

v0 treats Shopify `inventoryQuantity` as source of truth. In practice:
- ShipHero is the WMS and the real "inventory on shelf" source
- Shopify is the storefront's view of inventory
- The two can drift when toggles are changed in one system without the other
- Example: an item is oversold in ShipHero but Shopify still shows qty 10; OVERSELL RISK will not fire even though it should

**v0 mitigations:**
- Every OVERSELL RISK alert carries the v0 limitation footer instructing triage to verify against ShipHero
- Alerts never auto-take action on the storefront (no `publishedScope` toggling)
- Carlos @-mentioned on OVERSELL RISK owns ops/inventory reconciliation and is the point of contact for manual ShipHero verification

**v1 plan:** ShipHero Public API integration. Pull on-hand by SKU, three-way diff against Shopify and site state. Separate alert class: "INVENTORY DRIFT" fires on Shopify/ShipHero disagreement independent of ATC state. Requires ShipHero API key from ops.

### 13.2 Cloudflare / bot detection

Playwright from a Render datacenter IP may trigger WAF challenges. Mitigations in §5.4. Fallback: residential proxy. If crawl failure rate > 5% for a week, add proxy.

### 13.3 Instant Commerce page staleness

v0 relies on `sitemap.xml` for `/pages/*` enumeration. New pages take 24-48h to appear in the sitemap. Impact: a freshly-published landing page with a broken ATC won't be audited until the sitemap updates. Mitigation in v1: marketing-maintained list or Instant admin API.

### 13.4 Selector drift

If Instant Commerce or the Shopify theme ships a redesign that changes ATC selectors, the crawler starts reporting false `no_atc_element` for every page. Mitigation: selector regression test runs against a known-good PDP in CI nightly; alerts if the count of detected ATCs drops > 20% day-over-day.

### 13.5 State file corruption

If `alert-state.json` is wiped, next run re-alerts every active problem. Annoying but recoverable. Mitigated by Render Disk durability.

### 13.6 Rate limits under burst

If three jobs run concurrently (edge of 6h cycles), Shopify rate-limit headroom shrinks. Mitigation: cron schedules are offset (quantity at :00, ATC at 6am, weekly at 9am) so concurrency is rare. If it happens, jobs back off and retry.

---

## 14. Cutover plan

### Week 0 (build)
Repo scaffolded, Shopify app created, Slack app created, Render services deployed, all three jobs passing dry-run on dev store.

### Week 1 (parallel run)
All three jobs run on prod with `DRY_RUN=1`. Output is printed to Render logs and copied into a shared Slack DM for review (not posted to `#alerts-inventory`). Inventory Brain continues running as-is on colleague's laptop, owning the channel.

Parity check: every alert Inventory Brain posts should also appear in the Based Inventory dry-run output (modulo the new ATC audit layer and the singles-only correctness improvements). Any discrepancy is triaged and fixed.

### Week 2 (cutover)
Based Inventory flips to `DRY_RUN=0` and starts posting to `#alerts-inventory`. Inventory Brain is stopped on colleague's laptop. Based Inventory owns the channel.

Reset `alert-state.json` on cutover day so we don't double-alert the same tier transitions that Inventory Brain already reported.

### Week 3+ (steady state)
Monitor for 2 weeks. Capture feedback in GitHub issues. Plan v1 (ShipHero integration, dashboard).

---

## 15. Open items, not blocking build

1. **Mention routing for ATC alerts.** Proposal: OVERSELL RISK → Carlos; SALES LEAK + NO BUY BUTTON → Alex + Ryan. Confirm before first real post.
2. **Bot display name in Slack.** "Based Inventory" is the current plan. Confirm before Slack app install.
3. **Render account.** If no existing Render org for Based, create one under a team email (not a personal account) so it survives personnel changes. $21/mo billing owner TBD.
4. **Telegram fallback.** `openclaw` CLI on Render: needs either a Docker layer that installs it, or a replacement using direct Telegram Bot API HTTP calls. Prefer the latter (simpler, no external CLI dep). Small refactor from Inventory Brain's implementation.
5. **Heads-up to the colleague running Inventory Brain.** Coordination note, not a build task.

---

## 16. References

- Existing Inventory Brain source (local reference during build, not committed to this repo): `AGENTS.md`, `INVENTORY-RULES.md`, `check_inventory.py`, `weekly_audit.py`, `set-components.json`
- Original ATC vs Inventory Audit brief (Avi's research doc, local reference)
- [Shopify Admin GraphQL — InventoryLevel](https://shopify.dev/docs/api/admin-graphql/latest/objects/InventoryLevel)
- [Shopify Admin GraphQL — ProductVariant](https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariant)
- [Shopify API rate limits](https://shopify.dev/docs/api/usage/limits)
- [Playwright Python docs](https://playwright.dev/python/)
- [Playwright official Docker images](https://playwright.dev/python/docs/docker)
- [Render Cron Jobs](https://render.com/docs/cronjobs)
- [Render Disks](https://render.com/docs/disks)
