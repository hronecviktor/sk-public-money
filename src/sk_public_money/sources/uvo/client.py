"""Access to TED, the EU's Tenders Electronic Daily (https://ted.europa.eu).

Slovak buyers send every notice above the EU thresholds through ÚVO to TED. The Search API
(https://api.ted.europa.eu, POST /v3/notices/search) needs no key and takes TED's expert query syntax.
It allows 250 notices per page and 15 000 per query in page mode, and publishes no rate limit, so
requests are spaced 0.5 s apart. Search results flatten every field into lists, so each notice's
XML is read for anything that pairs lots, tenders and companies. Published notices never change (a
correction is a new notice), so their XML is cached for good. TED data may be reused free of charge
with attribution (Commission Decision 2011/833/EU).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ...http import SourceUnavailable, ThrottledClient

SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
NOTICE_URL = "https://ted.europa.eu/{lang}/notice/{number}/{kind}"
INTERVAL = 0.5
# Page mode returns at most this many notices for one query.
MAX_RESULTS = 15_000


def notice_url(number: str, lang: str = "sk") -> str:
    """The notice's page on TED."""
    return NOTICE_URL.format(lang=lang, number=number, kind="").rstrip("/")


class TEDClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None):
        super().__init__(lambda: INTERVAL, http=http, cache=cache)

    async def search(self, query: str, fields: list[str], *, page: int = 1, limit: int = 20) -> dict:
        """One page (1-based) of notices matching an expert query, with the given fields."""
        resp = await self._post(SEARCH_URL, {"query": query, "fields": fields, "page": page, "limit": limit})
        if resp.status_code >= 500 or resp.status_code == 429:
            raise SourceUnavailable(f"The TED API returned HTTP {resp.status_code}; try again later.")
        if resp.status_code == 400:
            try:
                message = resp.json().get("message") or resp.text
            except ValueError:
                message = resp.text
            raise ValueError(f"TED rejected the search: {message[:300]}")
        resp.raise_for_status()
        return resp.json()

    async def notice_xml(self, number: str) -> bytes:
        """The notice as published (eForms UBL or, before late 2023, the TED R2.0.9 schema)."""
        cached = self._cached(f"uvo/ted/{number}.xml")
        if cached.exists():
            return cached.read_bytes()
        resp = await self._get(NOTICE_URL.format(lang="en", number=number, kind="xml"))
        if resp.status_code >= 500 or resp.status_code == 429:
            raise SourceUnavailable(f"ted.europa.eu returned HTTP {resp.status_code}; try again later.")
        if resp.status_code == 404 or not resp.content.lstrip().startswith(b"<?xml"):
            raise LookupError(f"No notice {number} on TED.")
        resp.raise_for_status()
        self._store(cached, resp.content)
        return resp.content
