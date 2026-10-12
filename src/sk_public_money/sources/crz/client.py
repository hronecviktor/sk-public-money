"""HTTP access to crz.gov.sk that follows the portal's published robot rules.

https://www.crz.gov.sk/stahovanie-udajov-z-crz/ asks machines to make at most one request per
two seconds between 06:00 and 20:00 (Bratislava time) and fewer than three per second at night,
and blocks clients that go faster for up to an hour. Every request goes through one throttle.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from ...http import TZ, SourceUnavailable, ThrottledClient
from ...http import check_url as _check_url
from .parse import BASE_URL

DAY_INTERVAL = 2.1
NIGHT_INTERVAL = 0.5
ALLOWED_HOSTS = {"www.crz.gov.sk", "crz.gov.sk"}


class CRZUnavailable(SourceUnavailable):
    """The portal answered with its generic server error page (maintenance or a block)."""


def min_interval(now: datetime | None = None) -> float:
    hour = (now or datetime.now(TZ)).astimezone(TZ).hour
    return DAY_INTERVAL if 6 <= hour < 20 else NIGHT_INTERVAL


def check_url(url: str) -> str:
    return _check_url(url, BASE_URL, ALLOWED_HOSTS)


class CRZClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None):
        # Look min_interval up at call time so tests can patch it.
        super().__init__(lambda: min_interval(), http=http, cache=cache)

    @staticmethod
    def _is_server_error_page(resp: httpx.Response) -> bool:
        # During maintenance (and when blocking) the server returns a bare Apache-style
        # "404 Not Found" page instead of the portal's own not-found page.
        return resp.status_code >= 500 or (
            resp.status_code in (403, 404) and "<main" not in resp.text and len(resp.content) < 1000
        )

    async def get_html(self, path: str, params: dict | None = None) -> tuple[str, str]:
        """Returns (html, final_url). A portal 404 page is returned for the parser to report."""
        resp = await self._get(check_url(path), params)
        if self._is_server_error_page(resp):
            raise CRZUnavailable(
                f"crz.gov.sk returned HTTP {resp.status_code} without a portal page. The portal is "
                "probably in maintenance (it announces evening windows 20:00-23:59) or rate-limiting "
                "this client; try again later."
            )
        if resp.status_code not in (200, 404):
            resp.raise_for_status()
        return resp.text, str(resp.url)

    async def get_bytes(self, url: str, *, cache_key: str | None = None) -> bytes:
        """Download a file; with cache_key the result is stored on disk and reused."""
        cached = self._cached(cache_key)
        if cached and cached.exists():
            return cached.read_bytes()
        resp = await self._get(check_url(url))
        if resp.status_code == 404:
            raise FileNotFoundError(f"Not found on crz.gov.sk: {url}")
        if self._is_server_error_page(resp):
            raise CRZUnavailable(f"crz.gov.sk returned HTTP {resp.status_code} for {url}; try again later.")
        resp.raise_for_status()
        if cached:
            self._store(cached, resp.content)
        return resp.content

    async def get_export(self, day: date) -> bytes:
        today = datetime.now(TZ).date()
        if day >= today:
            newest = today - timedelta(days=1)
            raise ValueError(f"The export for {day} does not exist yet; the newest is for {newest} (built around 02:00).")
        return await self.get_bytes(f"/export/{day.isoformat()}.zip", cache_key=f"crz/export/{day.isoformat()}.zip")
