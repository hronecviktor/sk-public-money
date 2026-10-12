"""Access to the RPO REST API (https://api.statistics.sk/rpo/v1/).

RPO is run by the Ministry of Interior (MV SR), which documents the API at
https://rpo.minv.sk/rpo-api-doc.html; it is still served from the Statistical Office's host. No
authentication and no published limits. /search returns at most 500 records with no paging, and
compares IČO exactly but names in full text, ignoring case and diacritics (3 characters or more).
Responses take from under a second to over 30 s, so requests are spaced out gently.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ...http import SourceUnavailable, ThrottledClient

BASE_URL = "https://api.statistics.sk/rpo/v1"
INTERVAL = 0.5
MAX_RESULTS = 500  # /search never returns more


class RPOClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None):
        super().__init__(lambda: INTERVAL, http=http, cache=cache)

    async def get_json(self, path: str, params: dict | None = None) -> dict:
        resp = await self._get(f"{BASE_URL}/{path}", params)
        if resp.status_code >= 500:
            raise SourceUnavailable(f"api.statistics.sk returned HTTP {resp.status_code}; try again later.")
        if resp.status_code == 404:
            raise LookupError(f"Not found in RPO: {path}")
        if resp.status_code == 400:
            raise ValueError(f"RPO rejected the query: {resp.text[:300]}")
        resp.raise_for_status()
        return resp.json()

    async def search(self, **params: str) -> list[dict]:
        """Organizations matching every given /search filter (at most MAX_RESULTS)."""
        data = await self.get_json("search", {k: v for k, v in params.items() if v is not None})
        return data.get("results", [])

    async def entity(self, rpo_id: int) -> dict:
        """One organization with its full history."""
        return await self.get_json(f"entity/{rpo_id}", {"showHistoricalData": "true"})
