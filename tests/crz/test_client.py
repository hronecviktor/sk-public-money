from datetime import datetime

import httpx
import pytest
import respx

from sk_public_money.sources.crz import client as client_mod
from sk_public_money.sources.crz.client import CRZClient, CRZUnavailable, check_url, min_interval

MAINTENANCE_PAGE = (
    '<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML 2.0//EN">\n<html><head>\n<title>404 Not Found</title>\n'
    "</head><body>\n<h1>Not Found</h1>\n<p>The requested URL was not found on this server.</p>\n</body></html>\n"
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    monkeypatch.setattr(client_mod, "min_interval", lambda now=None: 0)


def test_rate_limit_by_time_of_day():
    assert min_interval(datetime(2026, 10, 12, 10, 0, tzinfo=client_mod.TZ)) == 2.1
    assert min_interval(datetime(2026, 10, 12, 21, 0, tzinfo=client_mod.TZ)) == 0.5
    assert min_interval(datetime(2026, 10, 12, 5, 59, tzinfo=client_mod.TZ)) == 0.5


@pytest.mark.parametrize("url", [
    "https://example.com/data/att/1.pdf",
    "https://www.crz.gov.sk.evil.com/x",
    "file:///etc/passwd",
])
def test_check_url_rejects_other_hosts(url):
    with pytest.raises(ValueError):
        check_url(url)


def test_check_url_normalises():
    assert check_url("/data/att/1.pdf") == "https://www.crz.gov.sk/data/att/1.pdf"
    assert check_url("http://crz.gov.sk/data/att/1.pdf") == "https://www.crz.gov.sk/data/att/1.pdf"


@pytest.mark.anyio
@respx.mock
async def test_maintenance_page_is_reported():
    respx.get("https://www.crz.gov.sk/123/").mock(return_value=httpx.Response(404, text=MAINTENANCE_PAGE))
    crz = CRZClient()
    with pytest.raises(CRZUnavailable):
        await crz.get_html("/123/")


@pytest.mark.anyio
@respx.mock
async def test_downloads_are_cached(tmp_path):
    route = respx.get("https://www.crz.gov.sk/export/2024-01-15.zip").mock(
        return_value=httpx.Response(200, content=b"zipdata")
    )
    crz = CRZClient(cache=tmp_path)
    assert await crz.get_bytes("/export/2024-01-15.zip", cache_key="crz/export/2024-01-15.zip") == b"zipdata"
    assert await crz.get_bytes("/export/2024-01-15.zip", cache_key="crz/export/2024-01-15.zip") == b"zipdata"
    assert route.call_count == 1
    assert (tmp_path / "crz" / "export" / "2024-01-15.zip").read_bytes() == b"zipdata"
