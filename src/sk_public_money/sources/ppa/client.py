"""Access to PPA's published lists of CAP beneficiaries on www.apa.sk.

The page below links one XLSX per financial year, in Slovak and English. The links go through a
file proxy (/api/files?url=…) whose path changes with each upload, so they are read from the page
each time. No API, no robots.txt and no published rate limits: requests are spaced 2 s apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from ...http import SourceUnavailable, ThrottledClient, check_url

BASE_URL = "https://www.apa.sk"
PAGE = BASE_URL + "/o-nas/zverejnovane-udaje/prehlad-prijimatelia-pomoci-z-epzf-epfrv"
HOSTS = {"www.apa.sk", "apa.sk"}
INTERVAL = 2.0
YEAR = re.compile(r"\b(20\d\d)\b")
DATE = re.compile(r"\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b")


def list_files(html: str) -> list[dict]:
    """The Slovak beneficiary lists on the page: [{year, title, published, url}], newest first."""
    soup = BeautifulSoup(html, "html.parser")
    files: dict[int, dict] = {}
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/api/files" not in href or ".xlsx" not in href.lower():
            continue
        box = link.find_parent(lambda tag: tag.find("h3") is not None)
        title = " ".join((box.find("h3").get_text() if box else link.get("aria-label") or "").split())
        # The English lists ("List of Subsidy Beneficiaries") repeat the Slovak ones.
        if "príjemc" not in title.lower() and "prijímat" not in title.lower():
            continue
        year = YEAR.search(title)
        if not year:
            continue
        dates = DATE.findall(box.get_text(" ") if box else "")
        published = f"{dates[0][2]}-{int(dates[0][1]):02d}-{int(dates[0][0]):02d}" if dates else None
        files.setdefault(int(year.group(1)), {
            "year": int(year.group(1)), "title": title, "published": published,
            "url": check_url(href, BASE_URL, HOSTS),
        })
    return [files[y] for y in sorted(files, reverse=True)]


class PPAClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None, interval: float = INTERVAL):
        super().__init__(lambda: interval, http=http, cache=cache)

    async def list_files(self) -> list[dict]:
        resp = await self._get(PAGE)
        if resp.status_code != 200:
            raise SourceUnavailable(f"www.apa.sk returned HTTP {resp.status_code} for the beneficiary page.")
        files = list_files(resp.text)
        if not files:
            raise SourceUnavailable("The PPA beneficiary page lists no XLSX files; its layout may have changed.")
        return files

    async def download(self, url: str, path: Path) -> Path:
        return await self._download(check_url(url, BASE_URL, HOSTS), path, expect=b"PK")
