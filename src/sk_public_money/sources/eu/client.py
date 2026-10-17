"""Access to the ITMS21+ open-data API (https://api.itms21.sk/public/v1), the monitoring system of
EU funds in Slovakia for the 2021-2027 period.

The API needs no key and publishes no rate limit or licence, so requests are spaced 1 s apart. Lists
take limit (-1 for all), offset, minId (inclusive in practice, although documented as "greater
than"), ascending and linkApi (only IDs for nested objects, which keeps codelists inlined but drops
the nested records). modifiedSince is documented but ignored: every list returns all its records, so
the sync reloads the lists in full. Dates are epoch milliseconds. Unknown paths answer HTTP 403 or
200 with only {"supportID": ...}; unknown IDs answer 404. The procurement list (and only it) calls
its records "result" instead of "results".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from ...http import SourceUnavailable, ThrottledClient

BASE_URL = "https://api.itms21.sk/public/v1"
WEB_URL = "https://www.itms21.sk"
INTERVAL = 1.0
PAGE = 1000


class ITMSClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None, interval: float = INTERVAL):
        super().__init__(lambda: interval, http=http, cache=cache)
        self.page = PAGE

    async def get_json(self, path: str, params: dict | None = None):
        resp = await self._get(f"{BASE_URL}/{path.lstrip('/')}", params)
        if resp.status_code == 404:
            raise LookupError(f"Not found in ITMS21+: {path}")
        if resp.status_code >= 500 or resp.status_code in (403, 429):
            raise SourceUnavailable(f"api.itms21.sk returned HTTP {resp.status_code} for {path}; try again later.")
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceUnavailable(f"api.itms21.sk sent no JSON for {path}.") from exc
        if isinstance(data, dict) and set(data) == {"supportID"}:
            raise SourceUnavailable(f"api.itms21.sk refused {path} (support ID {data['supportID']}).")
        return data

    async def pages(self, entity: str, params: dict | None = None) -> AsyncIterator[tuple[list[dict], int]]:
        """Every record of a list as (page of records, total count) pairs. Lists sorted by ID are paged
        by minId, so records added meanwhile are picked up and none are skipped; the subjekt list sorts
        by name whatever the parameters say, so lists like it are paged by offset, and records seen
        twice are dropped. The total is the first page's: with minId, the API counts only the records
        from there on."""
        min_id, offset, page, total, by_id = 0, 0, self.page, None, None
        seen: set[int] = set()
        while True:
            where = {"minId": min_id} if by_id is not False else {"offset": offset}
            data = await self.get_json(entity, {**(params or {}), "limit": page, "ascending": "true", **where})
            rows = data.get("results", data.get("result")) or []
            total = (data.get("size") or 0) if total is None else total
            if by_id is None:
                ids = [r["id"] for r in rows]
                by_id = ids == sorted(set(ids))
            fresh = [r for r in rows if r["id"] not in seen]
            seen.update(r["id"] for r in fresh)
            yield fresh, total
            if len(rows) < page:
                return
            min_id, offset = max(r["id"] for r in rows) + 1, offset + len(rows)

    async def project(self, ref: int | str) -> dict:
        """A project's detail by ID or code (e.g. 401501F545)."""
        path = f"projekt/id/{ref}" if isinstance(ref, int) or str(ref).isdigit() else f"projekt/{ref}"
        return await self.get_json(path, {"linkApi": "true"})

    async def procurement(self, procurement_id: int) -> dict:
        return await self.get_json(f"verejneobstaravanie/id/{procurement_id}")

    async def supplier(self, supplier_id: int) -> dict:
        """A supplier or buyer from the dodavatelobstaravatel register (its IDs differ from subjekt IDs)."""
        return await self.get_json(f"dodavatelobstaravatel/id/{supplier_id}")

    async def contract(self, contract_id: int) -> dict:
        return await self.get_json(f"zmluvaverejneobstaravanie/id/{contract_id}")

    async def project_invoices(self, project_id: int) -> list[dict]:
        data = await self.get_json("uctovnydoklad", {"projektId": project_id, "limit": -1, "linkApi": "true"})
        return data.get("results") or []
