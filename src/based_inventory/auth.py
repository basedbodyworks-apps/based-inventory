"""Shopify Admin API token acquisition via Client Credentials Grant.

Returns a 24h access token (shpat_...) suitable for use as the
X-Shopify-Access-Token header on Admin GraphQL requests. Applies to apps
created in a Dev Dashboard org that is linked to the merchant org containing
the target store. Tokens expire after 86399 seconds, so cron jobs should
fetch a fresh token at the start of each run.
"""

from __future__ import annotations

import requests


def fetch_access_token(
    store: str,
    client_id: str,
    client_secret: str,
    timeout: int = 10,
) -> str:
    """POST to /admin/oauth/access_token and return the access_token value.

    Raises RuntimeError on non-2xx or if access_token is missing from the body.
    """
    url = f"https://{store}/admin/oauth/access_token"
    response = requests.post(
        url,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Shopify OAuth failed: HTTP {response.status_code} {response.text}")
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"Shopify OAuth response missing access_token: {payload}")
    return token
