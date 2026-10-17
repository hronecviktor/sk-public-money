import httpx
import pytest

from sk_public_money import localdb, sync
from sk_public_money.http import SourceUnavailable, ThrottledClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_meta_and_refresh_interval(tmp_path):
    db = localdb.connect(tmp_path / "x" / "x.sqlite", "CREATE TABLE IF NOT EXISTS t (a);")
    assert localdb.get_meta(db, "k") is None
    assert localdb.is_due(db, "refreshed", 3600)
    localdb.mark_done(db, "refreshed")
    assert not localdb.is_due(db, "refreshed", 3600)
    assert localdb.is_due(db, "refreshed", 0)
    localdb.set_meta(db, "k", "v")
    assert localdb.get_meta(db, "k") == "v"


def test_free_space_check_refuses_a_download_that_does_not_fit(tmp_path):
    localdb.ensure_free_space(tmp_path / "new" / "file", 1, reserve=0)
    with pytest.raises(OSError, match="Not enough disk space"):
        localdb.ensure_free_space(tmp_path, 1 << 60)


@pytest.mark.anyio
async def test_download_streams_to_disk_and_checks_the_start(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"PK\x03\x04data" if request.url.path == "/a.zip" else b"<html>error</html>")

    client = ThrottledClient(lambda: 0, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), cache=tmp_path)
    path = await client._download("https://example.sk/a.zip", tmp_path / "a.zip", expect=b"PK")
    assert path.read_bytes() == b"PK\x03\x04data"
    with pytest.raises(SourceUnavailable, match="expected file"):
        await client._download("https://example.sk/b.zip", tmp_path / "b.zip", expect=b"PK")
    assert not (tmp_path / "b.zip").exists() and not (tmp_path / "b.zip.part").exists()


def test_sync_command_names_the_sources(capsys):
    with pytest.raises(SystemExit, match="sources: .*uvo"):
        sync.main([])
    with pytest.raises(SystemExit) as exc:
        sync.main(["uvo", "--help"])
    assert exc.value.code == 0
    assert "sk-public-money-sync uvo" in capsys.readouterr().out
