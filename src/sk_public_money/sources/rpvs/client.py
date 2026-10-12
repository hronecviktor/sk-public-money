"""Access to the RPVS open-data OData API (https://rpvs.gov.sk/opendatav2/).

The API needs no authentication. Its limits shape every query: $top is disabled (the server returns
pages of 20 with an @odata.nextLink), and $expand goes only one level deep. RPVS publishes no robot
rules, so requests are spaced out gently anyway.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import httpx

from ...http import SourceUnavailable, ThrottledClient
from ...http import check_url as _check_url
from ...text import letter_variants

BASE_URL = "https://rpvs.gov.sk/opendatav2"
WEB_URL = "https://rpvs.gov.sk/rpvs"
ALLOWED_HOSTS = {"rpvs.gov.sk"}
INTERVAL = 0.5


def odata_str(value: str) -> str:
    """Quote a string literal for an OData $filter."""
    return "'" + value.replace("'", "''") + "'"


# RPVS rejects filters with more than 100 expression nodes. A checked letter costs 5-8 nodes (each
# spelling in its in-list counts), so a filter fits about 14 letters. Callers start there, retry with
# fewer letters on QueryTooComplex, and verify the unchecked rest locally with the same folding.
MAX_LETTERS = 14


class QueryTooComplex(ValueError):
    """RPVS rejected a filter for exceeding its expression node limit."""


def spelled_like(field: str, value: str, *, prefix: bool = False, max_letters: int | None = None) -> str:
    """$filter matching field to value in any case and with any diacritics, letter by letter.

    RPVS compares strings exactly and has no accent-insensitive function, and listing every spelling
    explodes ('Šimečková' has hundreds). Instead each position is checked against that letter's
    variants: substring(f,0,1) in ('s','š') and substring(f,1,1) in ('i','í') ... which grows only
    linearly with the name. With prefix=True, field only has to start with value. With max_letters,
    only that many leading letters are checked, so the filter is a superset of the exact match.
    """
    value = unicodedata.normalize("NFC", value.strip())
    expr = f"tolower(trim({field}))"
    clauses = [] if prefix else [f"length(trim({field})) eq {len(value)}"]
    for i, ch in enumerate(value[:max_letters]):
        spellings = letter_variants(ch)
        if len(spellings) == 1:
            clauses.append(f"substring({expr},{i},1) eq {odata_str(spellings)}")
        else:
            clauses.append(f"substring({expr},{i},1) in ({','.join(odata_str(s) for s in spellings)})")
    return "(" + " and ".join(clauses) + ")"


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
            if "node count limit" in message:
                raise QueryTooComplex(message)
            raise ValueError(f"RPVS rejected the query: {message}")
        resp.raise_for_status()
        return resp.json()

    async def get_rows(
        self, entity_set: str, params: dict, max_rows: int, *, keep=None, max_pages: int = 25
    ) -> tuple[list[dict], int | None, bool]:
        """Follow server paging until max_rows rows (that pass keep, if given) are collected.

        Returns (rows, server-side count, whether more rows may exist). With keep, the server count
        includes rows that keep rejects, so it is only an upper bound.
        """
        data = await self.get_json(entity_set, {**params, "$count": "true"})
        total = data.get("@odata.count")
        rows: list[dict] = []
        pages = 1
        while True:
            rows.extend(r for r in data.get("value", []) if keep is None or keep(r))
            if len(rows) >= max_rows or not data.get("@odata.nextLink") or pages >= max_pages:
                break
            data = await self.get_json(data["@odata.nextLink"])
            pages += 1
        more = len(rows) > max_rows or bool(data.get("@odata.nextLink"))
        return rows[:max_rows], total, more
