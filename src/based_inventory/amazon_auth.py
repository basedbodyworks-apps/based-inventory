"""Amazon Login With Amazon (LWA) token refresh for SP-API.

SP-API authentication is a refresh-token flow, similar to ShipHero:
- The seller authorizes the app once in Seller Central → produces a
  long-lived `refresh_token`.
- Each API call needs a short-lived `access_token` (1 hour TTL).
- We exchange refresh_token for access_token via Amazon's OAuth endpoint.

We don't persist the access_token; we get a fresh one at the start of
each job run. 1-hour TTL is plenty for any single bot execution.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"


def fetch_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    timeout: int = 30,
) -> str:
    """Exchange an LWA refresh_token for a short-lived access_token.

    Returns the access_token string. Raises RuntimeError on failure.
    """
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        r = requests.post(LWA_TOKEN_URL, data=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"LWA token request failed: {exc}") from exc

    if r.status_code != 200:
        body = r.text[:500]
        raise RuntimeError(f"LWA token exchange failed with HTTP {r.status_code}: {body}")
    data: dict[str, Any] = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"LWA response missing access_token: {data}")
    return token
