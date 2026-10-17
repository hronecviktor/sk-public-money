import json
import sqlite3

import httpx
import pytest

from sk_public_money import bundle, sync
from sk_public_money.http import ThrottledClient
from sk_public_money.sources.uvo import index as uvo_index


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SK_PUBLIC_MONEY_BUNDLE_URL", "https://example.sk/rel/")
    return tmp_path / "cache"


def build_index(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); CREATE TABLE t (a);")
    db.executemany("INSERT INTO meta VALUES (?, ?)", [("checked_at", "123"), ("keep_raw", "1")])
    db.execute("INSERT INTO t VALUES ('a fictional notice')")
    db.commit()
    db.close()


def serve(files):
    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if request.url.path.startswith("/rel/") and name in files:
            return httpx.Response(200, content=files[name])
        return httpx.Response(404)

    return ThrottledClient(lambda: 0, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def snapshot_files(cache, tmp_path):
    build_index(uvo_index.default_path())
    manifest = bundle.build(tmp_path / "out", ["uvo"])
    files = {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()}
    uvo_index.default_path().unlink()
    return json.loads(manifest.read_text()), files


def test_snapshot_round_trip(cache, tmp_path):
    manifest, files = snapshot_files(cache, tmp_path)
    entry = manifest["sources"]["uvo"]
    assert entry["file"] == "uvo.sqlite.xz" and entry["schema"] == uvo_index.SCHEMA_VERSION and "CC BY" in entry["licence"]

    assert bundle.bootstrap("uvo", serve(files))
    db = sqlite3.connect(uvo_index.default_path())
    assert db.execute("SELECT a FROM t").fetchone() == ("a fictional notice",)
    meta = dict(db.execute("SELECT key, value FROM meta"))
    assert meta == {"keep_raw": "0"}  # checked_at dropped: the tools look for new issues at once
    assert not list(uvo_index.default_path().parent.glob("*.xz")) and not list(uvo_index.default_path().parent.glob("*.part"))

    assert not bundle.bootstrap("uvo", serve(files))  # an existing index is never replaced
    assert not bundle.bootstrap("ppa", serve(files))  # not bundled


@pytest.mark.parametrize("break_it", ["schema", "hash", "missing", "manifest"])
def test_a_bad_snapshot_falls_back_to_a_full_build(cache, tmp_path, break_it, capsys):
    manifest, files = snapshot_files(cache, tmp_path)
    if break_it == "schema":
        manifest["sources"]["uvo"]["schema"] += 1
    elif break_it == "hash":
        manifest["sources"]["uvo"]["sha256"] = "0" * 64
    elif break_it == "missing":
        del files["uvo.sqlite.xz"]
    else:
        del files["manifest.json"]
    if "manifest.json" in files:
        files["manifest.json"] = json.dumps(manifest).encode()
    assert not bundle.bootstrap("uvo", serve(files))
    assert not uvo_index.default_path().exists()
    assert "building it from the source instead" in capsys.readouterr().err


def test_sync_starts_from_the_snapshot_unless_told_not_to(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(bundle, "bootstrap", lambda name: calls.append(name))

    class Fake:
        def main(self, argv):
            calls.append(("main", argv))

    monkeypatch.setattr(sync.importlib, "import_module", lambda name: Fake())
    sync.main(["uvo", "--since", "2026-01-01"])
    sync.main(["uvo", "--no-bootstrap"])
    others = ",".join(name for name in sync.SOURCES if name != "uvo")
    sync.main(["all", "--no-bootstrap", "--skip", others])
    sync.main(["all", "--skip", others])
    assert calls == ["uvo", ("main", ["--since", "2026-01-01"]), ("main", []), ("main", []), "uvo", ("main", [])]
