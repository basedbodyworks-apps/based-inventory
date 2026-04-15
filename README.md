# Based Inventory

Daily bot that monitors Shopify inventory and audits rendered Add-to-Cart state on basedbodyworks.com. Posts alerts to Slack `#alerts-inventory`.

See `docs/specs/2026-04-15-based-inventory-design.md` for the full design.

## Local development

    python3.12 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements-dev.txt
    playwright install chromium
    cp .env.example .env
    # Edit .env with real tokens

    # Run one job locally (dry-run by default via DRY_RUN=1 in .env)
    python -m based_inventory.jobs.quantity_alerts

## Tests

    pytest

## Lint

    ruff check
    ruff format

## Jobs

- `based_inventory.jobs.quantity_alerts` (every 6h)
- `based_inventory.jobs.atc_audit` (daily 6am PST, 13:00 UTC)
- `based_inventory.jobs.weekly_snapshot` (Fridays 9am PST, 16:00 UTC)

Each reads env vars (see `.env.example`) and posts to Slack unless `DRY_RUN=1`.
