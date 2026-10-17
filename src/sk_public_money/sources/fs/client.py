"""Downloads of the Finančná správa SR open-data lists.

The lists are zipped XML files at https://report.financnasprava.sk/<list>.zip, replaced daily,
monthly or quarterly (Last-Modified tells when); no key is needed. Older quarterly files of the
corporate income tax list are linked from an archive page on www.financnasprava.sk. The server sits
behind an F5 firewall and sometimes resets a transfer midway, so downloads are retried. No rate limit
or robots rules are published (robots.txt is a 404); requests are spaced 2 s apart.

Data: CC BY 4.0, "Zdroj údajov: Finančná správa SR - https://opendata.financnasprava.sk/" (the
reliability index file itself says CC0).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx

from ...http import SourceUnavailable, ThrottledClient

REPORT_URL = "https://report.financnasprava.sk"
ARCHIVE_PAGE = "https://www.financnasprava.sk/sk/elektronicke-sluzby/verejne-sluzby/zoznamy/archiv-vybranych-informacnych/zoznam-subjektov-s-vyskou-dane"
ARCHIVE_LINK = re.compile(r"https://report\.financnasprava\.sk/archiv/DS_DPPOS/ds_dppos_(\d{8})\.zip")
INTERVAL = 2.0
RETRIES = 3
RETRY_WAIT = 10.0


def list_url(name: str) -> str:
    return f"{REPORT_URL}/{name}.zip"


class FSClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, interval: float = INTERVAL, retry_wait: float = RETRY_WAIT):
        super().__init__(lambda: interval, http=http)
        self._retry_wait = retry_wait

    async def last_modified(self, url: str) -> tuple[str | None, int | None]:
        """The Last-Modified header and size of a file, without downloading it."""
        resp = await self._request("HEAD", url)
        if resp.status_code != 200:
            raise SourceUnavailable(f"report.financnasprava.sk returned HTTP {resp.status_code} for {url}.")
        size = resp.headers.get("content-length")
        return resp.headers.get("last-modified"), int(size) if size and size.isdigit() else None

    async def download(self, url: str, path: Path) -> Path:
        """Download a zip, retrying transfers the server resets."""
        for attempt in range(1, RETRIES + 1):
            try:
                return await self._download(url, path, expect=b"PK")
            except SourceUnavailable as exc:
                # Only network failures are worth another try; a wrong status or file is not.
                if attempt == RETRIES or "Could not download" not in str(exc):
                    raise
                await asyncio.sleep(self._retry_wait * attempt)
        raise AssertionError("unreachable")

    async def archive_files(self) -> list[tuple[str, str]]:
        """The archived corporate income tax files as (YYYY-MM-DD, url), oldest first."""
        resp = await self._get(ARCHIVE_PAGE)
        if resp.status_code != 200:
            raise SourceUnavailable(f"www.financnasprava.sk returned HTTP {resp.status_code} for the archive page.")
        found = {m.group(1): m.group(0) for m in ARCHIVE_LINK.finditer(resp.text)}
        return [(f"{d[:4]}-{d[4:6]}-{d[6:]}", url) for d, url in sorted(found.items())]
