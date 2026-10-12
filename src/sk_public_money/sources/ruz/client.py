"""Access to the RÚZ open API (https://www.registeruz.sk/cruz-public/home/api).

The API needs no key and its data are CC0. It publishes no rate limits, and robots.txt only disallows
the XLS exports, so requests are spaced 0.5 s apart. Every detail is one request per entity,
statement or report, so detail responses are cached on disk: templates for good (RÚZ never deletes or
changes them), statements and reports for a few days.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from ...http import USER_AGENT, SourceUnavailable, ThrottledClient

BASE_URL = "https://www.registeruz.sk/cruz-public"
API_URL = BASE_URL + "/api"
INTERVAL = 0.5
DETAIL_TTL = 3 * 24 * 3600
CODEBOOK_TTL = 30 * 24 * 3600
# The portal's firewall answers 403 on its web paths (the name search) to a User-Agent with an apostrophe.
RUZ_USER_AGENT = USER_AGENT.replace("'", "")


class RUZClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None):
        super().__init__(lambda: INTERVAL, http=http, cache=cache)
        if http is None:
            self._http.headers["User-Agent"] = RUZ_USER_AGENT

    async def get_json(self, url: str, params: dict | None = None, *, cache_key: str | None = None, ttl: float | None = None):
        """GET a JSON document; with cache_key it is kept on disk (for ttl seconds, or for good)."""
        cached = self._cached(cache_key)
        if cached and cached.exists() and (ttl is None or time.time() - cached.stat().st_mtime < ttl):
            try:
                return json.loads(cached.read_bytes())
            except ValueError:
                pass  # a damaged cache file is fetched again
        resp = await self._get(url, params)
        if resp.status_code >= 500 or resp.status_code in (403, 429):
            raise SourceUnavailable(f"registeruz.sk returned HTTP {resp.status_code}; try again later.")
        if resp.status_code == 404:
            raise LookupError(f"Not found in RÚZ: {url.removeprefix(BASE_URL)} {params or ''}".strip())
        if resp.status_code == 400:
            raise ValueError(f"RÚZ rejected the request: {resp.text[:300] or 'HTTP 400'}")
        resp.raise_for_status()
        data = resp.json()
        if cached:
            self._store(cached, resp.content)
        return data

    async def entity_ids(self, ico: str) -> list[int]:
        """IDs of every accounting entity (účtovná jednotka) registered under an IČO, deleted ones included."""
        data = await self.get_json(f"{API_URL}/uctovne-jednotky", {"zmenene-od": "2000-01-01", "ico": ico})
        return data.get("id", [])

    async def entity(self, entity_id: int) -> dict:
        return await self.get_json(f"{API_URL}/uctovna-jednotka", {"id": entity_id})

    async def statement(self, statement_id: int) -> dict:
        return await self.get_json(
            f"{API_URL}/uctovna-zavierka", {"id": statement_id}, cache_key=f"ruz/zavierka/{statement_id}.json", ttl=DETAIL_TTL
        )

    async def report(self, report_id: int) -> dict:
        return await self.get_json(
            f"{API_URL}/uctovny-vykaz", {"id": report_id}, cache_key=f"ruz/vykaz/{report_id}.json", ttl=DETAIL_TTL
        )

    async def template(self, template_id: int) -> dict:
        return await self.get_json(f"{API_URL}/sablona", {"id": template_id}, cache_key=f"ruz/sablona/{template_id}.json")

    async def codebook(self, name: str) -> dict[str, str]:
        """A ŠÚSR code list (e.g. 'pravne-formy') as {code: Slovak name}."""
        data = await self.get_json(f"{API_URL}/{name}", cache_key=f"ruz/ciselnik/{name}.json", ttl=CODEBOOK_TTL)
        return {item["kod"]: item["nazov"].get("sk") for item in data.get("klasifikacie", data.get("lokacie", []))}

    async def suggest(self, query: str) -> list[dict]:
        """The web search box's suggestions: at most 10 entities whose name, IČO or DIČ match."""
        data = await self.get_json(f"{BASE_URL}/domain/suggestion/search", {"query": query})
        return data if isinstance(data, list) else []
