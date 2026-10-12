"""HTTP access to crz.gov.sk that follows the portal's published robot rules.

https://www.crz.gov.sk/stahovanie-udajov-z-crz/ asks machines to make at most one request per
two seconds between 06:00 and 20:00 (Bratislava time) and fewer than three per second at night,
and blocks clients that go faster for up to an hour. Every request goes through one throttle.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx

from . import __version__
from .parse import BASE_URL

TZ = ZoneInfo("Europe/Bratislava")
DAY_INTERVAL = 2.1
NIGHT_INTERVAL = 0.5
ALLOWED_HOSTS = {"www.crz.gov.sk", "crz.gov.sk"}


class CRZUnavailable(Exception):
    """The portal answered with its generic server error page (maintenance or a block)."""


def min_interval(now: datetime | None = None) -> float:
    hour = (now or datetime.now(TZ)).astimezone(TZ).hour
    return DAY_INTERVAL if 6 <= hour < 20 else NIGHT_INTERVAL


def cache_dir() -> Path:
    base = os.environ.get("CRZ_MCP_CACHE_DIR")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(xdg) / "crz-mcp"


def check_url(url: str) -> str:
    """Resolve a path or URL against the portal and refuse anything outside it."""
    full = urljoin(BASE_URL + "/", url)
    parsed = urlparse(full)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"Only crz.gov.sk URLs are allowed, got: {url}")
    return parsed._replace(scheme="https", netloc="www.crz.gov.sk").geturl()


class CRZClient:
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None):
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
            headers={"User-Agent": f"crz-mcp/{__version__} (MCP server; follows the CRZ robot rate limits)"},
        )
        self._cache = cache or cache_dir()
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        async with self._lock:
            wait = self._last + min_interval() - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._http.get(url, params=params)
            finally:
                self._last = time.monotonic()

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
        cached = self._cache / cache_key if cache_key else None
        if cached and cached.exists():
            return cached.read_bytes()
        resp = await self._get(check_url(url))
        if resp.status_code == 404:
            raise FileNotFoundError(f"Not found on crz.gov.sk: {url}")
        if self._is_server_error_page(resp):
            raise CRZUnavailable(f"crz.gov.sk returned HTTP {resp.status_code} for {url}; try again later.")
        resp.raise_for_status()
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(cached.suffix + ".part")
            tmp.write_bytes(resp.content)
            tmp.replace(cached)
        return resp.content

    async def get_export(self, day: date) -> bytes:
        today = datetime.now(TZ).date()
        if day >= today:
            newest = today - timedelta(days=1)
            raise ValueError(f"The export for {day} does not exist yet; the newest is for {newest} (built around 02:00).")
        return await self.get_bytes(f"/export/{day.isoformat()}.zip", cache_key=f"export/{day.isoformat()}.zip")
