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
    with pytest.raises(SystemExit, match=r"sources: (\w+, )*uvo\b"):
        sync.main([])
    with pytest.raises(SystemExit) as exc:
        sync.main(["uvo", "--help"])
    assert exc.value.code == 0
    assert "sk-public-money-sync uvo" in capsys.readouterr().out


def test_sync_all_runs_every_source_and_reports_failures(monkeypatch, capsys):
    ran = []

    class Fake:
        def __init__(self, name, fail=None):
            self.name, self.fail = name, fail

        def main(self, argv):
            ran.append((self.name, argv))
            if self.fail == "exit":
                raise SystemExit(1)
            if self.fail == "error":
                raise RuntimeError("portal down")
            if self.fail == "exit0":
                raise SystemExit(0)

    fakes = {"a": Fake("a"), "b": Fake("b", "error"), "c": Fake("c", "exit0"), "d": Fake("d", "exit")}
    monkeypatch.setattr(sync, "SOURCES", {name: name for name in fakes})
    monkeypatch.setattr(sync.importlib, "import_module", lambda name: fakes[name])
    with pytest.raises(SystemExit) as exc:
        sync.main(["all", "--skip", "c"])
    assert exc.value.code == 1
    assert ran == [("a", []), ("b", []), ("d", [])]
    err = capsys.readouterr().err
    assert "a: ok" in err and "b: failed: RuntimeError: portal down" in err and "d: failed (exit 1)" in err

    ran.clear()
    fakes = {"a": Fake("a"), "c": Fake("c", "exit0")}
    monkeypatch.setattr(sync, "SOURCES", {name: name for name in fakes})
    sync.main(["all"])  # everything ok: no exit
    assert [name for name, _ in ran] == ["a", "c"]

    with pytest.raises(SystemExit):
        sync.main(["all", "--skip", "nope"])


def test_build_hints_work_for_uvx_installs():
    hint = localdb.sync_command("fs")
    assert hint.startswith("`uvx --from git+https://github.com/hronecviktor/sk-public-money sk-public-money-sync fs`")
    from sk_public_money.sources.fs.tools import NO_INDEX
    from sk_public_money.sources.ppa.tools import NOT_BUILT
    from sk_public_money.sources.uvo.tools import NO_INDEX_NOTE

    assert all("uvx --from" in message for message in (NO_INDEX, NOT_BUILT, NO_INDEX_NOTE))
