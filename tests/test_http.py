import httpx
import pytest

from sk_public_money.http import SourceUnavailable, ThrottledClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_timeout_is_reported_with_a_reason(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout("", request=request)

    client = ThrottledClient(lambda: 0, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), cache=tmp_path)
    with pytest.raises(SourceUnavailable, match=r"Could not reach example\.sk \(timed out\)"):
        await client._get("https://example.sk/x")
