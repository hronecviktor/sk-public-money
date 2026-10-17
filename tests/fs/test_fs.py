"""Finančná správa tests: parsing the list files, the index, the sync and the tools. The XML files in
fixtures/ are fictional taxpayers in the shape of the real report.financnasprava.sk files (padded
quotes in names, DDMMYYYY list dates, a 12-digit unit IČO, a nonsense violation year, a record with
no taxpayer, an archive file in Windows-1250)."""

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from sk_public_money.sources.fs import client as client_mod
from sk_public_money.sources.fs import parse, sync, tools
from sk_public_money.sources.fs.index import FSIndex

FIXTURES = Path(__file__).parent / "fixtures"
LISTS = ["ds_dppos", "ds_dphno", "ds_dsdd", "ds_dphz", "ds_dphv", "ds_dphs", "ds_iz_ran"]


def zipped(name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name.split('_2')[0]}.xml", (FIXTURES / f"{name}.xml").read_bytes())
        z.writestr(f"{name.split('_2')[0]}.xsd", b"<xs:schema/>")
    return buf.getvalue()


def write_zip(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.zip"
    path.write_bytes(zipped(name))
    return path


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def ix(tmp_path, monkeypatch):
    """An index of every fixture list (the archive file last, to check that its older names do not replace newer ones)."""
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path / "cache"))
    index = FSIndex()
    for name in LISTS:
        index.load(name, write_zip(tmp_path, name), last_modified="Sat, 17 Oct 2026 00:35:45 GMT")
    index.load("ds_dppos", write_zip(tmp_path, "ds_dppos_20230630"), archive=True)
    index.load("ds_dphno", write_zip(tmp_path, "ds_dphno_20260630"), archive=True)
    index.set_meta("inline_checked_at", "9999999999")  # no inline download in tool tests
    monkeypatch.setattr(tools, "_index", index)
    yield index
    index.close()


# --- parsing -----------------------------------------------------------------------------------------


def test_value_cleaning():
    assert parse.clean_name('" CCL s.r.o. "') == '"CCL s.r.o."'
    assert parse.clean_name("„ A. Kmeťa 8 \"") == "„A. Kmeťa 8\""
    assert parse.clean_name('" BUMA - R ", s.r.o.') == '"BUMA - R ", s.r.o.'
    assert parse.parse_date("07.10.2026") == "2026-10-07"
    assert parse.parse_date("17102026") == "2026-10-17"
    assert parse.parse_date("31.02.2026") == "31.02.2026"
    assert parse.cents("1522.86") == 152286 and parse.cents("x") is None
    assert parse.ico("1234567") == "01234567" and parse.ico("005843630008") == "005843630008" and parse.ico("0") is None
    assert parse.dic_of("SK2020999001") == "2020999001" and parse.dic_of("CZ12345678") is None
    assert parse.vat_period("0326") == "2026-03" and parse.vat_period("Q126") == "2026-Q1" and parse.vat_period("2024") == "2024"
    assert parse.violation_year("2026") == 2026 and parse.violation_year("525") == "525"
    assert parse.place(" 141", "94636", "Kravany nad Dunajom", "SK") == "141, 94636 Kravany nad Dunajom"
    assert parse.place("ul. X 1", "30-504", "KRAKÓW", "Poľsko") == "ul. X 1, 30-504 KRAKÓW, Poľsko"


def test_list_file_streams_items_and_reads_its_date(tmp_path):
    source = parse.ListFile(write_zip(tmp_path, "ds_dphs"))
    items = list(source)
    assert source.as_of == "2026-10-17"
    assert len(items) == 4 and "IC_DPH" not in items[0]
    assert items[1]["NAZOV_DS"] == '" ALFA TEST s.r.o. "'


def test_windows_1250_archive_file(tmp_path):
    source = parse.ListFile(write_zip(tmp_path, "ds_dppos_20230630"))
    assert next(iter(source))["NAZOV_DS"] == "ALFA TEST s.r.o. (starý názov Žilina)"
    assert source.as_of == "2023-06-15"


# --- index -------------------------------------------------------------------------------------------


def test_index_counts_and_dates(ix):
    lists = ix.lists()
    assert lists["ds_dphs"]["records"] == 3 and lists["ds_dphs"]["skipped"] == 1
    # The archive file adds tax rows but does not replace the current list's date.
    assert lists["ds_dppos"]["as_of"] == "2026-09-15"


def test_every_published_corporate_tax_row_is_kept(ix, tmp_path):
    expected = [
        ("2025-12-31", 192000, 0, "2026-09-15"),
        ("2022-12-31", 0, 25000, "2026-09-15"),  # an additional assessment in a later list
        ("2022-12-31", 50000, 0, "2023-06-15"),
    ]
    rows = ix.cit(["2020999001"])
    assert [(r["period_to"], r["tax"], r["additional"], r["release"]) for r in rows] == expected
    ix.load("ds_dppos", write_zip(tmp_path, "ds_dppos"))  # indexing a file again adds nothing
    assert len(ix.cit(["2020999001"])) == 3


def test_reloading_a_list_replaces_it(ix, tmp_path):
    ix.load("ds_dsdd", write_zip(tmp_path, "ds_dsdd"))
    rows, total = ix.search_debtors("alfa", town=None, min_amount=None, limit=20, offset=0)
    assert total == 1


# --- tools -------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_taxpayer(ix):
    out = await tools.fs_get_taxpayer("012 345 67")
    assert out["taxpayers"] == [{
        "dic": "2020999001", "ico": "01234567", "name": '"ALFA TEST s.r.o."', "address": "Fiktívna 1/A, 01001 Žilina",
        "reliability_index": "menej spoľahlivý",
    }]
    assert out["vat_registration"][0]["kind"] == "§4" and out["vat_registration"][0]["vat_payer_from"] == "2002-06-01"
    assert out["vat_cancellation_reasons"] == [{"ic_dph": "SK2020999001", "name": "ALFA TEST s.r.o.", "address": "Fiktívna 1/A, 01001 Žilina",
                                                "violation_year": "2026", "published": "2026-10-09"}]
    assert "vat_struck_off" not in out
    assert out["corporate_income_tax"][0] == {"period": "2025-01-01 – 2025-12-31", "tax_assessed": 1920.0, "list_of": "2026-09-15"}
    assert out["corporate_income_tax"][1] == {"period": "2022-01-01 – 2022-12-31", "tax_assessed": 0.0, "additional_tax_assessed": 250.0, "list_of": "2026-09-15"}
    assert out["corporate_income_tax"][2]["tax_loss"] == 3400.1 and out["corporate_income_tax"][2]["tax_assessed"] == 500.0
    assert "several quarterly lists" in out["corporate_income_tax_note"]
    # Newest period first (a quarter sorts by its last month), a repeated period with its lists.
    assert out["vat_returns"] == [
        {"period": "2026-03", "excess_deduction": 1522.86, "list_of": "2026-09-15"},
        {"period": "2026-03", "excess_deduction": 1400.0, "list_of": "2026-06-15"},
        {"period": "2026-Q1", "own_tax_liability": 517.25},
        {"period": "2025-12", "excess_deduction": 100.0},
    ]
    assert "several quarterly lists" in out["vat_returns_note"]
    assert "2026-06-15 to 2026-09-15" in out["vat_returns_coverage"]
    debtors = out["tax_debtor_list"]
    assert debtors["matched_by"] == "name" and debtors["entries"][0]["debt"] == 7603.08
    assert out["lists_as_of"]["ds_iz_ran"] == "2026-10-16"
    assert "Finančná správa SR" in out["source"]
    # The same taxpayer by DIČ and IČ DPH.
    assert (await tools.fs_get_taxpayer("2020999001"))["taxpayers"] == out["taxpayers"]
    assert (await tools.fs_get_taxpayer("SK2020999001"))["ico"] == "01234567"


@pytest.mark.anyio
async def test_vat_group_representative(ix):
    out = await tools.fs_get_taxpayer("99999004")
    assert out["vat_registration"] == "not in the VAT register"
    group = out["vat_group_represented"]
    assert group["matched_by"] == "name"
    assert group["groups"][0]["vat_registration"][0]["ic_dph"] == "SK7120999003"
    assert group["group_vat_returns"][0] == {"dic": "7120999003", "period": "2026-05", "own_tax_liability": 100000.5}


@pytest.mark.anyio
async def test_struck_off_person_and_unit_ico(ix):
    out = await tools.fs_get_taxpayer("99999006")
    assert out["vat_struck_off"][0]["violation_year"] == "525" and out["vat_struck_off"][0]["struck_off"] == "2022-02-11"
    unit = await tools.fs_get_taxpayer("999990050001")
    assert unit["corporate_income_tax"][0]["tax_loss"] == 12.5


@pytest.mark.anyio
async def test_get_taxpayer_errors(ix):
    with pytest.raises(Exception, match="none of the Finančná správa lists"):
        await tools.fs_get_taxpayer("87654321")
    with pytest.raises(Exception, match="Expected an IČO"):
        await tools.fs_get_taxpayer("abc")


@pytest.mark.anyio
async def test_no_index(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tools, "_index", None)
    with pytest.raises(Exception, match="sk-public-money-sync fs"):
        await tools.fs_get_taxpayer("01234567")


@pytest.mark.anyio
async def test_search_debtors_ignores_diacritics(ix):
    out = await tools.fs_search_debtors("Siroky")
    assert out["matched_by"] == "name" and out["total"] == 2
    assert [r["name"] for r in out["results"]] == ["Jozef Široký", "Jozef Siroky"]  # largest debt first
    assert (await tools.fs_search_debtors("široký", town="krakow"))["results"][0]["debt"] == 198.36
    assert (await tools.fs_search_debtors("siroky", min_debt=500))["total"] == 1
    assert out["list_as_of"] == "2026-09-30"


@pytest.mark.anyio
async def test_list_flagged(ix):
    struck = await tools.fs_list_flagged("struck_off", town="zilina")
    assert [r["ico"] for r in struck["results"]] == ["99999007"]
    dated = await tools.fs_list_flagged("struck_off", date_from="2022-01-01", date_to="2022-12-31")
    assert [r["name"] for r in dated["results"]] == ["Mária Vzorová"]
    less = await tools.fs_list_flagged("less_reliable")
    assert less["results"] == [{"ico": "01234567", "dic": "2020999001", "name": '"ALFA TEST s.r.o."', "address": "Fiktívna 1/A, 01001 Žilina"}]
    with pytest.raises(Exception, match="no dates"):
        await tools.fs_list_flagged("less_reliable", date_from="2026-01-01")


# --- sync --------------------------------------------------------------------------------------------


def portal(calls: list, *, flaky: set | None = None):
    """A mocked report.financnasprava.sk and archive page. flaky: URLs whose first GET fails."""
    flaky = set(flaky or ())

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append((request.method, url))
        for name, page in client_mod.ARCHIVE_PAGES.items():
            if url == page:
                links = "".join(f'<a href="https://report.financnasprava.sk/archiv/{name.upper()}/{name}_{d}.zip">x</a>'
                                for d in ("20170630", "20230630", "20260630"))
                return httpx.Response(200, text=f"<html>{links}</html>")
        name = url.rsplit("/", 1)[1].removesuffix(".zip")
        if not (FIXTURES / f"{name}.xml").exists():
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"last-modified": "Sat, 17 Oct 2026 00:35:45 GMT", "content-length": "1000"})
        if url in flaky:
            flaky.discard(url)
            raise httpx.ReadError("Connection reset by peer")
        return httpx.Response(200, content=zipped(name))

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_sync_builds_skips_unchanged_and_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    calls: list = []
    current = "https://report.financnasprava.sk/ds_dphs.zip"
    make = lambda: client_mod.FSClient(http=httpx.AsyncClient(transport=portal(calls, flaky={current})), interval=0, retry_wait=0)  # noqa: E731
    out = io.StringIO()
    failed = await sync.run(LISTS, "2018-01-01", out=out, client=make())
    # The fixture portal lacks the 2023 VAT returns and 2026 corporate tax archive files.
    assert sorted(f.split(":")[0] for f in failed) == ["ds_dphno 2023-06-30", "ds_dppos 2026-06-30"]
    gets = [u for m, u in calls if m == "GET"]
    assert gets.count(current) == 2  # reset once, then retried
    assert not any("20170630" in u for u in gets)  # before --since
    ix = FSIndex()
    assert json.loads(ix.get_meta("ds_dppos_archive")) == ["2023-06-30"]
    assert json.loads(ix.get_meta("ds_dphno_archive")) == ["2026-06-30"]
    assert len(ix.cit(["2020999001"])) == 3
    assert len(ix.vat_returns(["2020999001"])) == 4
    ix.close()
    assert not list((tmp_path / "fs" / "raw").glob("*"))  # zips deleted after indexing

    calls.clear()
    await sync.run(LISTS, "2018-01-01", out=io.StringIO(), client=make())
    # Nothing changed: only HEAD requests, and another try at the archive files that failed.
    gets = [u.rsplit("/", 1)[1] for m, u in calls if m == "GET" and "report." in u]
    assert sorted(gets) == ["ds_dphno_20230630.zip", "ds_dppos_20260630.zip"]
    assert [m for m, u in calls if "report." in u].count("HEAD") == len(LISTS)


@pytest.mark.anyio
async def test_inline_refresh_only_fetches_the_small_flag_lists(ix, monkeypatch):
    calls: list = []
    monkeypatch.setattr(tools, "_client", client_mod.FSClient(http=httpx.AsyncClient(transport=portal(calls)), interval=0))
    ix.set_meta("inline_checked_at", "0")
    ix.db.execute("UPDATE lists SET last_modified = 'old' WHERE name IN ('ds_dphz', 'ds_dphv', 'ds_dphs')")
    await tools.fs_get_taxpayer("01234567")
    assert sorted({u.rsplit("/", 1)[1] for _, u in calls}) == ["ds_dphv.zip", "ds_dphz.zip"]
    calls.clear()
    await tools.fs_get_taxpayer("01234567")
    assert calls == []  # checked recently


def test_sync_command_options(capsys):
    with pytest.raises(SystemExit) as exc:
        sync.main(["--help"])
    assert exc.value.code == 0
    assert "--since" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        sync.main(["--lists", "ds_nope"])


@pytest.mark.anyio
async def test_vat_history(ix):
    out = await tools.fs_get_taxpayer("01234567", vat_history=True)
    assert len(out["vat_returns"]) == 4 and "vat_returns_note" in out
    group = (await tools.fs_get_taxpayer("99999004", vat_history=True))["vat_group_represented"]
    assert group["group_vat_returns"][0]["period"] == "2026-05"


def test_old_index_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE lists (name TEXT PRIMARY KEY, as_of TEXT, last_modified TEXT, records INTEGER, skipped INTEGER, indexed_at REAL);
        CREATE TABLE vat_returns (dic TEXT, period TEXT, excess INTEGER, own INTEGER, PRIMARY KEY (dic, period)) WITHOUT ROWID;
        INSERT INTO vat_returns VALUES ('2020999001', '2026-03', 1, 0);
        INSERT INTO lists VALUES ('ds_dphno', '2026-09-15', 'x', 1, 0, 0), ('ds_dphs', '2026-10-17', 'x', 1, 0, 0);
        INSERT INTO meta VALUES ('dppos_archive', '["2023-06-30"]');
    """)
    db.close()
    ix = FSIndex(path)
    assert ix.vat_returns(["2020999001"]) == []
    assert set(ix.lists()) == {"ds_dphs"}  # the VAT returns list is fetched again by the next sync
    assert ix.get_meta("ds_dppos_archive") == '["2023-06-30"]' and ix.get_meta("dppos_archive") is None
    ix.close()
