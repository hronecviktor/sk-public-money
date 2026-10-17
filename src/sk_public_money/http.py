"""Shared HTTP plumbing: one throttled client per source, on-disk cache, URL allow-listing."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx

from . import __version__

TZ = ZoneInfo("Europe/Bratislava")
# No apostrophes: registeruz.sk's firewall answers 403 to a User-Agent that contains one.
USER_AGENT = f"sk-public-money/{__version__} (MCP server; follows the robot rate limits of each portal)"


class SourceUnavailable(Exception):
    """A portal is down, in maintenance, or blocking this client."""


def cache_dir() -> Path:
    base = os.environ.get("SK_PUBLIC_MONEY_CACHE_DIR")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(xdg) / "sk-public-money"


def check_url(url: str, base_url: str, allowed_hosts: set[str]) -> str:
    """Resolve a path or URL against base_url and refuse any host outside allowed_hosts."""
    full = urljoin(base_url + "/", url)
    parsed = urlparse(full)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in allowed_hosts:
        raise ValueError(f"Only {urlparse(base_url).hostname} URLs are allowed, got: {url}")
    return parsed._replace(scheme="https", netloc=urlparse(base_url).netloc).geturl()


class ThrottledClient:
    """Sends a source's requests one at a time, at least min_interval() seconds apart."""

    def __init__(
        self,
        min_interval: Callable[[], float],
        http: httpx.AsyncClient | None = None,
        cache: Path | None = None,
    ):
        self._min_interval = min_interval
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._cache = cache or cache_dir()
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        return await self._request("GET", url, params=params)

    async def _post(self, url: str, json: dict) -> httpx.Response:
        return await self._request("POST", url, json=json)

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        async with self._lock:
            wait = self._last + self._min_interval() - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._http.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                # Timeouts carry no message, so name the error kind instead of printing nothing.
                reason = "timed out" if isinstance(exc, httpx.TimeoutException) else (str(exc) or type(exc).__name__)
                raise SourceUnavailable(
                    f"Could not reach {urlparse(url).hostname} ({reason}). The portal may be down; try again later."
                ) from exc
            finally:
                self._last = time.monotonic()

    async def _download(self, url: str, path: Path, *, headers: dict | None = None, expect: bytes | None = None) -> Path:
        """Stream a large file to path, spaced like every other request; a failed download leaves
        nothing behind. expect: the bytes the file must start with (e.g. b"PK" for a zip)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        host = urlparse(url).hostname
        async with self._lock:
            wait = self._last + self._min_interval() - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._http.stream("GET", url, headers=headers) as resp:
                    if resp.status_code != 200:
                        raise SourceUnavailable(f"{host} returned HTTP {resp.status_code} for {url}.")
                    with tmp.open("wb") as out:
                        head = b""
                        async for chunk in resp.aiter_bytes(1 << 16):
                            if expect and len(head) < len(expect):
                                head += chunk[: len(expect)]
                                if len(head) >= len(expect) and not head.startswith(expect):
                                    raise SourceUnavailable(f"{host} did not send the expected file for {url}.")
                            out.write(chunk)
                tmp.replace(path)
            except httpx.TransportError as exc:
                reason = "timed out" if isinstance(exc, httpx.TimeoutException) else (str(exc) or type(exc).__name__)
                raise SourceUnavailable(f"Could not download from {host} ({reason}); try again later.") from exc
            finally:
                tmp.unlink(missing_ok=True)
                self._last = time.monotonic()
        return path

    def _cached(self, cache_key: str | None) -> Path | None:
        return self._cache / cache_key if cache_key else None

    def _store(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(content)
        tmp.replace(path)
