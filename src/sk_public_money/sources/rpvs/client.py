"""Access to the RPVS open-data OData API (https://rpvs.gov.sk/opendatav2/).

The API needs no authentication. Its limits shape every query: $top is disabled (the server returns
pages of 20 with an @odata.nextLink), and $expand goes only one level deep. RPVS publishes no robot
rules, so requests are spaced out gently anyway.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ...http import SourceUnavailable, ThrottledClient
from ...http import check_url as _check_url

BASE_URL = "https://rpvs.gov.sk/opendatav2"
WEB_URL = "https://rpvs.gov.sk/rpvs"
ALLOWED_HOSTS = {"rpvs.gov.sk"}
INTERVAL = 0.5


def odata_str(value: str) -> str:
    """Quote a string literal for an OData $filter."""
    return "'" + value.replace("'", "''") + "'"


class RPVSClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None):
        super().__init__(lambda: INTERVAL, http=http, cache=cache)

    async def get_json(self, path_or_url: str, params: dict | None = None) -> dict:
        url = _check_url(path_or_url, BASE_URL, ALLOWED_HOSTS) if path_or_url.startswith("http") else f"{BASE_URL}/{path_or_url}"
        resp = await self._get(url, params)
        if resp.status_code >= 500:
            raise SourceUnavailable(f"rpvs.gov.sk returned HTTP {resp.status_code}; try again later.")
        if resp.status_code == 404:
            raise LookupError(f"Not found in RPVS: {path_or_url}")
        if resp.status_code == 400:
            try:
                message = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = resp.text[:300]
            raise ValueError(f"RPVS rejected the query: {message}")
        resp.raise_for_status()
        return resp.json()

    async def get_rows(self, entity_set: str, params: dict, max_rows: int) -> tuple[list[dict], int | None]:
        """Follow server paging until max_rows rows are collected. Returns (rows, total count if known)."""
        data = await self.get_json(entity_set, {**params, "$count": "true"})
        total = data.get("@odata.count")
        rows = list(data.get("value", []))
        while len(rows) < max_rows and data.get("@odata.nextLink"):
            data = await self.get_json(data["@odata.nextLink"])
            rows.extend(data.get("value", []))
        return rows[:max_rows], total
