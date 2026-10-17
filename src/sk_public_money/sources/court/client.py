"""Access to Slovak court data: the Ministry of Justice ISU (Infosúd) API and Súdna rada's list of
judges' asset declarations.

The ISU API (https://obcan.justice.sk/pilot/api/ress-isu-service/v3/api-docs) needs no
authentication and publishes no rate limits or licence; it is labelled "pilot". Its paging is
1-based (page 0 and 1 both return the first page). Decision PDFs live under
obcan.justice.sk/content/public/item/<id>; robots.txt disallows /content, but the user decided they
may be fetched one at a time on demand. Súdna rada (www.sudnarada.gov.sk) publishes no robot rules.
Without published limits every request is spaced by at least a second.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ...http import SourceUnavailable, ThrottledClient

API_URL = "https://obcan.justice.sk/pilot/api/ress-isu-service/v1"
CONTENT_URL = "https://obcan.justice.sk/content/public/item/"
SUDNARADA_URL = "https://www.sudnarada.gov.sk"
DECLARATIONS_PATH = "/9054-sk/majetkove-priznania-sudcov-slovenskej-republiky/"
INTERVAL = 1.0
PDF_MEMORY = 8  # recently read PDFs kept in memory, so reading further pages costs no request


class CourtClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None):
        # Looked up at call time so tests can patch INTERVAL.
        super().__init__(lambda: INTERVAL, http=http, cache=cache)
        self._pdfs: OrderedDict[str, bytes] = OrderedDict()

    async def api(self, path: str, params: dict | None = None) -> dict:
        resp = await self._get(f"{API_URL}/{path}", params)
        if resp.status_code == 404:
            raise LookupError(_message(resp) or f"Not found in the court register: {path}")
        if resp.status_code >= 500:
            message = _message(resp)
            # The search backend reports query syntax errors as HTTP 500 and echoes its Solr query.
            if "SyntaxError" in message:
                raise ValueError("The court search could not parse the query; remove quotes, brackets and operators like OR.")
            raise SourceUnavailable(f"obcan.justice.sk returned HTTP {resp.status_code}; try again later.")
        if resp.status_code == 400:
            raise ValueError(f"The court register rejected the query: {_message(resp)[:300]}")
        resp.raise_for_status()
        return resp.json()

    async def disqualification_list(self) -> list[dict]:
        """Every record of the register of disqualified persons (about a thousand): name, address,
        decision date and sometimes the court. The ban period is only in each record's detail."""
        rows: list[dict] = []
        page = 1
        while True:
            data = await self.api("diskvalifikacia", {"size": 500, "page": page})
            batch = data.get("diskvalifikaciaList") or []
            rows += batch
            if not batch or len(rows) >= (data.get("numFound") or 0) or page >= 20:
                return rows
            page += 1

    async def pdf(self, url: str) -> bytes:
        """A decision PDF from obcan.justice.sk/content/public/item/ or an asset declaration from
        www.sudnarada.gov.sk/data/att/; other URLs are refused."""
        url = check_pdf_url(url)
        if url in self._pdfs:
            self._pdfs.move_to_end(url)
            return self._pdfs[url]
        resp = await self._get(url)
        host = urlparse(url).hostname
        if resp.status_code == 404:
            raise LookupError(f"Not found on {host}: {url}")
        if resp.status_code >= 500:
            raise SourceUnavailable(f"{host} returned HTTP {resp.status_code} for {url}; try again later.")
        resp.raise_for_status()
        if not resp.content.startswith(b"%PDF"):
            # Súdna rada answers unknown attachment paths with its home page instead of a 404.
            raise LookupError(f"{host} did not return a PDF for {url}; the link may be outdated.")
        self._pdfs[url] = resp.content
        while len(self._pdfs) > PDF_MEMORY:
            self._pdfs.popitem(last=False)
        return resp.content

    async def declarations_page(self, name: str, page: int = 0) -> str:
        """One page (25 rows) of Súdna rada's asset-declaration list filtered by name; page is 0-based."""
        params = {"nazov": name, "art_rok_podania": "0"}
        if page:
            params["page"] = str(page)
        resp = await self._get(SUDNARADA_URL + DECLARATIONS_PATH, params)
        if resp.status_code >= 400:
            raise SourceUnavailable(f"www.sudnarada.gov.sk returned HTTP {resp.status_code}; try again later.")
        return resp.text


def check_pdf_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else SUDNARADA_URL + url if url.startswith("/data/att/") else url)
    host, path = parsed.hostname, parsed.path
    if host == "obcan.justice.sk" and path.startswith("/content/public/item/"):
        return f"https://obcan.justice.sk{path}"
    if host in ("www.sudnarada.gov.sk", "sudnarada.gov.sk") and path.startswith("/data/att/") and path.endswith(".pdf"):
        return f"{SUDNARADA_URL}{path}"
    raise ValueError(
        "Only decision PDFs (https://obcan.justice.sk/content/public/item/...) and asset declarations "
        f"(https://www.sudnarada.gov.sk/data/att/....pdf) can be read, got: {url}"
    )


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message") or "")
    except ValueError:
        return resp.text[:300]
