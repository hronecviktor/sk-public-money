"""Access to political-party money: Slovenská sporiteľňa transparent accounts and the annual reports
(výročné správy) that parties file with the State Commission for Elections (minv.sk).

Transparent accounts: Slovenská sporiteľňa publishes every transparent account it runs (parties,
candidates, campaigns, but also charities and collections) through an unauthenticated JSON API
behind its web page https://www.slsp.sk/sk/ludia/transparentne-ucty. The API serves no robots.txt
and no terms of use were found, so requests are kept small and at least 1 s apart. Turnovers name
the counterparty as the bank shows it, with amount, note and variable symbol; no counterparty IBAN
or IČO. Other banks' transparent accounts (ČSOB, VÚB, Tatra banka, Fio) are not covered.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from ...http import SourceUnavailable, ThrottledClient

SLSP_ACCOUNTS_URL = "https://api.slsp.sk/ta-accounts/api/accounts"
SLSP_TURNOVERS_URL = "https://api.slsp.sk/ta-turnovers/api/accounts/{iban}/turnovers"
SLSP_WEB_URL = "https://www.slsp.sk/sk/ludia/transparentne-ucty"
INTERVAL = 1.0
# The account list (about 1 200 accounts, 3 requests) is fetched again after this many seconds.
ACCOUNTS_MAX_AGE = 24 * 3600
ACCOUNTS_PAGE = 500
# The largest turnover page asked for.
MAX_PAGE = 500


class SLSPClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None, interval: float = INTERVAL):
        super().__init__(lambda: interval, http=http, cache=cache)

    async def _json(self, url: str, params: dict) -> dict:
        resp = await self._get(url, params)
        if resp.status_code >= 500 or resp.status_code == 429:
            raise SourceUnavailable(f"api.slsp.sk returned HTTP {resp.status_code}; try again later.")
        if resp.status_code == 400:
            raise ValueError(f"Slovenská sporiteľňa rejected the query ({resp.text[:200]}).")
        try:
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise SourceUnavailable(f"api.slsp.sk sent an unexpected answer (HTTP {resp.status_code}); try again later.") from exc

    async def accounts(self) -> list[dict]:
        """Every published transparent account (raw API records), cached for a day."""
        cached = self._cached("party/slsp_accounts.json")
        if cached.exists() and time.time() - cached.stat().st_mtime < ACCOUNTS_MAX_AGE:
            return json.loads(cached.read_text())
        rows: list[dict] = []
        page = 0
        while True:
            data = await self._json(SLSP_ACCOUNTS_URL, {"search": "", "size": ACCOUNTS_PAGE, "page": page})
            rows += data.get("accounts", [])
            pages = (data.get("page") or {}).get("totalPages") or 0
            page += 1
            if page >= pages or page >= 20:
                break
        self._store(cached, json.dumps(rows, ensure_ascii=False).encode())
        return rows

    async def turnovers(
        self, iban: str, *, page: int = 0, size: int = 50, counterparty: str | None = None,
        date_from: str | None = None, date_to: str | None = None,
        amount_from: float | None = None, amount_to: float | None = None,
    ) -> dict:
        """One page of an account's turnovers, newest first. Amounts are signed euros (negative =
        outgoing). counterparty is matched by the bank in the counterparty name, ignoring case and
        diacritics."""
        params: dict = {"size": min(size, MAX_PAGE), "page": page}
        if counterparty:
            params["search"] = counterparty
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        if amount_from is not None:
            params["amountFrom"] = f"{amount_from:.2f}"
        if amount_to is not None:
            params["amountTo"] = f"{amount_to:.2f}"
        return await self._json(SLSP_TURNOVERS_URL.format(iban=iban), params)


# --- annual reports (výročné správy) on minv.sk -------------------------------------------------

MINV_URL = "https://www.minv.sk"
REPORTS_OVERVIEW = MINV_URL + "/?vyrocnespravyps"
REPORTS_INDEX = MINV_URL + "/?vyrocne-spravy-politickych-stran-a-politickych-hnuti-za-rok-{year}"
REPORTS_PATH = "/swift_data/source/statna_komisia_pre_volby/30_annual_report/"
MINV_INTERVAL = 1.5


class MinvClient(ThrottledClient):
    """The State Commission's pages on minv.sk: an overview page linking one index page per year, and
    one PDF per party and year. robots.txt disallows only /?poslat-stranku; no rate limit is
    published, so requests are spaced 1.5 s apart and PDFs fetched one at a time, on demand."""

    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None, interval: float = MINV_INTERVAL):
        super().__init__(lambda: interval, http=http, cache=cache)

    async def page(self, url: str) -> str:
        if not url.startswith(MINV_URL + "/?"):
            raise ValueError(f"Not a minv.sk page: {url}")
        resp = await self._get(url)
        if resp.status_code == 404:
            raise LookupError(f"minv.sk has no page {url}.")
        if resp.status_code != 200:
            raise SourceUnavailable(f"minv.sk returned HTTP {resp.status_code} for {url}; try again later.")
        return resp.text

    async def download_pdf(self, url: str, path: Path) -> Path:
        if not url.startswith(MINV_URL + REPORTS_PATH):
            raise ValueError(f"Not an annual-report address on minv.sk: {url}")
        return await self._download(url, path, expect=b"%PDF")
