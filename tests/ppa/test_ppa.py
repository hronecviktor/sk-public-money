"""PPA tests. The workbooks are built here in the two layouts PPA has published (FY2025 and FY2024,
headers and quirks copied from the real files) with fictional beneficiaries; page.html is the real
download section of www.apa.sk. RPO is mocked with a fictional company."""

import io
from pathlib import Path

import httpx
import openpyxl
import pytest

from sk_public_money.sources.ppa import client as client_mod
from sk_public_money.sources.ppa import parse, sync, tools
from sk_public_money.sources.ppa.index import PPAIndex
from sk_public_money.sources.rpo import client as rpo_client_mod
from sk_public_money.sources.rpo import tools as rpo_tools

FIXTURES = Path(__file__).parent / "fixtures"
TITLE = ("PRÍLOHA VIII vykonávacieho nariadenia Komisie (EÚ) 2022/128, ktorým sa stanovujú pravidlá uplatňovania "
         "nariadenia (EÚ) Európskeho parlamentu a Rady (EÚ) 2021/2116 ...")
HEAD = ["Meno prijímateľa/názov právneho subjektu/názov združenia", "Priezvisko prijímateľa",
        "Ak patrí k skupine, názov materskej spoločnosti a jej identifikačné číslo pre DPH alebo daňové identifikačné číslo",
        "Obec", "Kód opatrenia/typu intervencie/sektora podľa prílohy IX", "Špecifický cieľ "]
AMOUNTS = ["Suma za operáciu v rámci EPZF", "Celková suma z EPZF pre daného prijímateľa",
           "Suma za operáciu v rámci EPFRV", "Celková suma z EPFRV pre daného prijímateľa",
           "Suma za operáciu v rámci spolufinancovania", "Celková spolufinancovaná suma pre daného prijímateľa",
           "Súčet sumy z EPFRV a spolufinancovaných súm",
           "Celková suma z prostriedkov EÚ a spolufinancovaná suma pre daného prijímateľa"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def workbook(year: int, header: list, rows: list) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "FY 2024"  # the FY2025 file carries this sheet name too
    for row in [[TITLE], [], [f"Prijímatelia pomoci z EPZF a EPFRV - finančný rok {year}"], [], header + [None], *rows, [], [None] * 5]:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def op25(name, surname, parent, obec, code, so, epzf=None, epfrv=None, cofin=None):
    rd = (epfrv or 0) + (cofin or 0) or None
    return [name, surname, parent, obec, code, so, epzf, None, epfrv, None, cofin, None, rd, (epzf or 0) + (rd or 0)]


def sub25(name, epzf, epfrv, cofin):
    return [name, None, None, None, None, None, None, epzf, None, epfrv, None, cofin, epfrv + cofin, epzf + epfrv + cofin]


FY2025 = workbook(2025, HEAD + AMOUNTS, [
    op25("Fiktívne družstvo Horná Lehota ", None, "", "Horná Lehota", "I.1", "SO1", epzf=1000.5),
    op25("Fiktívne družstvo Horná Lehota ", None, "", "Horná Lehota", "V.2", "SO1", epfrv=300, cofin=200),
    op25("Fiktívne družstvo Horná Lehota ", None, "", "Horná Lehota", "(prázdne)", "(prázdne)", epfrv=50, cofin=10),
    sub25("Fiktívne družstvo Horná Lehota ", 1000.5, 350, 210),
    op25("Jana", "Vymyslená", None, "Dolná Ves", "I.4", "S04", epzf=2000),
    op25("Jana", "Vymyslená", None, "Dolná Ves", "I.1", "SO1", epzf=900),
    sub25(" Jana Vymyslená ", 2900, 0, 0),
    op25(123456, None, None, "Malá Obec", "I.1", "SO1", epzf=800),
    sub25(123456, 800, 0, 0),
    op25("Fiktívny Agro s.r.o.", None, "FIKTÍVNY HOLDING a.s. /  / CZ99999999", "Bratislava-Ružinov", "II.2.", "SO1", epzf=500),
    op25("Fiktívny Agro s.r.o.", None, "FIKTÍVNY HOLDING a.s. /  / CZ99999999", "Bratislava-Ružinov", "VI.4", None, epfrv=-75, cofin=-25),
    op25("Fiktívny Agro s.r.o.", None, "FIKTÍVNY HOLDING a.s. /  / CZ99999999", "Horná Lehota", "V.2", "SO1", epfrv=3000, cofin=1000),
    sub25("Fiktívny Agro s.r.o.", 500, 2925, 975),
])


def op24(name, surname, obec, code, so, epzf=0, epfrv=0, cofin=0):
    return [name, surname, "", obec, code, so, None, None, epzf, "", epfrv, "", cofin, "", epfrv + cofin, ""]


def sub24(name, surname, obec, epzf, epfrv, cofin):
    return [name, surname, None, obec, None, None, None, None, None, epzf, None, epfrv, None, cofin, None, epzf + epfrv + cofin]


FY2024 = workbook(2024, HEAD[:6] + ["Dátum začatia   ", "Dátum ukončenia "] + AMOUNTS, [
    op24("Jana", "Vymyslená", "Dolná Ves", "I.1", "SO1", epzf=1500),
    sub24("Jana", "Vymyslená", "Dolná Ves", 1500, 0, 0),
    op24("654321", "anonymizované", "Okres Komárno", "I.1", "SO1", epzf=300),
    sub24("654321", "anonymizované", "Okres Komárno", 300, 0, 0),
    op24("PD Fiktívne s. r. o.", "", "Fiktívna", " VI.4", None, epfrv=750, cofin=250),
    op24("Poľnohospodárske družstvo Fiktívne", "", "Fiktívna", "II.11.", None, epzf=-40),
    sub24("Poľnohospodárske družstvo Fiktívne", "", "Fiktívna", -40, 750, 250),
])


def read(data: bytes, tmp_path: Path) -> tuple[dict, list[dict]]:
    path = tmp_path / "list.xlsx"
    path.write_bytes(data)
    meta, rows = parse.read(path)
    return meta, list(rows)


def test_parse_fy2025(tmp_path):
    meta, rows = read(FY2025, tmp_path)
    assert meta["fiscal_year"] == 2025  # from the title row; the sheet says "FY 2024"
    assert [b["name"] for b in rows] == ["Fiktívne družstvo Horná Lehota", "Jana Vymyslená", "123456", "Fiktívny Agro s.r.o."]
    assert [b["kind"] for b in rows] == ["entity", "natural_person", "anonymised", "entity"]
    coop, jana, _, agro = rows
    assert coop["total"] == 1560.5 and coop["epzf"] == 1000.5 and coop["cofinancing"] == 210
    assert "published_total" not in coop  # matches the subtotal row
    assert [a["code"] for a in coop["amounts"]] == ["I.1", "V.2", None]  # "(prázdne)" is no code
    assert jana["amounts"][0]["objectives"] == ["SO4"]  # "S04" with a zero
    assert agro["parent"] == {"name": "FIKTÍVNY HOLDING a.s.", "ids": ["CZ99999999"]}
    assert agro["places"] == ["Bratislava-Ružinov", "Horná Lehota"]
    assert {a["code"] for a in agro["amounts"]} == {"II.2", "VI.4", "V.2"}
    assert agro["total"] == 4400


def test_parse_fy2024(tmp_path):
    meta, rows = read(FY2024, tmp_path)
    assert meta["fiscal_year"] == 2024
    jana, anon, coop = rows
    assert jana["kind"] == "natural_person" and jana["name"] == "Jana Vymyslená"
    assert anon["kind"] == "anonymised" and anon["places"] == ["Okres Komárno"]
    # Operations spell the name two ways; the subtotal row's spelling wins.
    assert coop["name"] == "Poľnohospodárske družstvo Fiktívne" and coop["also_named"] == ["PD Fiktívne s. r. o."]
    assert sorted(a["code"] for a in coop["amounts"]) == ["II.11", "VI.4"]
    assert coop["total"] == 960


def test_parent_ids_keep_slashes_inside_numbers():
    assert parse.parse_parent("Fiktiv GmbH / DE999999999 / 156/116/00000") == {"name": "Fiktiv GmbH", "ids": ["DE999999999", "156/116/00000"]}
    assert parse.parse_parent("") is None


def test_wrong_layout_is_a_format_error(tmp_path):
    path = tmp_path / "x.xlsx"
    path.write_bytes(workbook(2025, ["Meno prijímateľa", "Obec"], []))
    with pytest.raises(parse.FormatError):
        parse.read(path)


def test_list_files():
    files = client_mod.list_files((FIXTURES / "page.html").read_text(encoding="utf-8"))
    assert [(f["year"], f["published"]) for f in files] == [(2025, "2026-04-23"), (2024, "2025-07-29")]
    assert all(f["url"].startswith("https://www.apa.sk/api/files?url=") for f in files)
    assert "EPFRV_FY%25202025.xlsx" in files[0]["url"]


def portal(files: dict[str, bytes], page: str | None = None):
    page = page or (FIXTURES / "page.html").read_text(encoding="utf-8")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/api/files":
            for key, data in files.items():
                if key in str(request.url):
                    return httpx.Response(200, content=data)
            return httpx.Response(404)
        return httpx.Response(200, text=page)

    return client_mod.PPAClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), interval=0), calls


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.anyio
async def test_sync_builds_tops_up_and_mirrors(cache):
    out = io.StringIO()
    client, calls = portal({"FY%25202025": FY2025, "FY%25202024": FY2024})
    done = await sync.sync(out=out, client=client)
    assert [d["year"] for d in done] == [2025, 2024]
    assert not list((cache / "ppa").glob("*.xlsx"))  # downloads are deleted once indexed
    idx = PPAIndex()
    assert {y: r["beneficiaries"] for y, r in idx.years().items()} == {2024: 3, 2025: 4}
    idx.close()

    client, calls = portal({})
    assert await sync.sync(out=out, client=client) == []
    assert len(calls) == 1  # only the page: both links unchanged

    # A year PPA stops publishing leaves the index too.
    page = (FIXTURES / "page.html").read_text(encoding="utf-8").replace("EPFRV 2024", "EPFRV").replace("FY%25202024", "FYold")
    client, _ = portal({}, page)
    await sync.sync(out=out, client=client)
    idx = PPAIndex()
    assert list(idx.years()) == [2025]
    idx.close()


@pytest.mark.anyio
async def test_sync_rejects_mislabelled_file(cache):
    out = io.StringIO()
    client, _ = portal({"FY%25202025": FY2024, "FY%25202024": FY2024})
    await sync.sync(out=out, client=client)
    assert "STOPPED" in out.getvalue() and "titled financial year 2024" in out.getvalue()


@pytest.fixture
def ix(tmp_path, monkeypatch):
    idx = PPAIndex(tmp_path / "ppa.sqlite")
    for year, data in ((2025, FY2025), (2024, FY2024)):
        _, rows = read(data, tmp_path)
        idx.replace_year(year, rows)
    monkeypatch.setattr(tools, "_index", idx)
    yield idx
    idx.close()


@pytest.mark.anyio
async def test_missing_index_says_how_to_build(cache, monkeypatch):
    monkeypatch.setattr(tools, "_index", None)
    with pytest.raises(Exception, match="sk-public-money-sync ppa"):
        await tools.ppa_top()


@pytest.mark.anyio
async def test_search_by_name(ix):
    res = await tools.ppa_search_beneficiary(name="vymyslena")
    assert [(b["fiscal_year"], b["name"]) for b in res["beneficiaries"]] == [(2025, "Jana Vymyslená"), (2024, "Jana Vymyslená")]
    first = res["beneficiaries"][0]
    assert first["ico"] is None and first["matched_by"] == "name"
    assert [m["code"] for m in first["by_measure"]] == ["I.4", "I.1"]
    assert first["by_measure"][0]["objectives"] == ["SO4: climate change mitigation and adaptation"]

    res = await tools.ppa_search_beneficiary(name="fiktivny holding")
    assert res["beneficiaries"][0]["name"] == "Fiktívny Agro s.r.o."
    assert res["beneficiaries"][0]["matched_in"] == "parent_company"

    res = await tools.ppa_search_beneficiary(name="fiktivne", obec="Fiktívna", year=2024)
    assert [b["name"] for b in res["beneficiaries"]] == ["Poľnohospodárske družstvo Fiktívne"]
    with pytest.raises(Exception, match="2024, 2025"):
        await tools.ppa_search_beneficiary(name="x", year=2019)


def rpo_mock(monkeypatch, records):
    def handler(request):
        assert request.url.params["identifier"] == "01234567"
        return httpx.Response(200, json={"results": records})

    monkeypatch.setattr(rpo_client_mod, "INTERVAL", 0)
    monkeypatch.setattr(rpo_tools, "_client", rpo_client_mod.RPOClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))


@pytest.mark.anyio
async def test_search_by_ico_matches_rpo_names(ix, monkeypatch):
    rpo_mock(monkeypatch, [{
        "id": 1, "identifiers": [{"value": "01234567", "validFrom": "2001-01-01"}],
        "fullNames": [{"value": "Agro Fiktív s.r.o.", "validFrom": "2001-01-01", "validTo": "2015-01-01"},
                      {"value": "FIKTÍVNY AGRO, s. r. o.", "validFrom": "2015-01-02"}],
        "addresses": [{"validFrom": "2001-01-01", "street": "Poľná", "buildingNumber": "1", "postalCodes": ["82100"],
                       "municipality": {"value": "Bratislava-Ružinov"}, "country": {"code": "703"}}],
    }])
    res = await tools.ppa_search_beneficiary(ico="1234567")
    assert res["rpo"]["name"] == "FIKTÍVNY AGRO, s. r. o." and res["rpo"]["former_names"] == ["Agro Fiktív s.r.o."]
    [hit] = res["beneficiaries"]
    assert hit["name"] == "Fiktívny Agro s.r.o." and hit["ico"] == "01234567" and hit["matched_by"] == "name"
    assert hit["seat_obec_matches"] is True


@pytest.mark.anyio
async def test_top(ix):
    res = await tools.ppa_top()
    assert res["fiscal_year"] == 2025 and res["beneficiaries"] == 4 and res["sum_total"] == 9660.5
    assert [r["name"] for r in res["ranking"]] == ["Fiktívny Agro s.r.o.", "Jana Vymyslená", "Fiktívne družstvo Horná Lehota", "123456"]

    res = await tools.ppa_top(measure="v.2", obec="Horná Lehota")
    assert res["measure"]["name"].startswith("Rural development: natural")
    assert [(r["name"], r["total"]) for r in res["ranking"]] == [("Fiktívny Agro s.r.o.", 4000), ("Fiktívne družstvo Horná Lehota", 500)]
    assert res["ranking"][0]["total_all_measures_and_places"] == 4400

    res = await tools.ppa_top(obec="Bratislava")  # finds Bratislava-Ružinov
    assert [(r["name"], r["total"]) for r in res["ranking"]] == [("Fiktívny Agro s.r.o.", 400)]

    res = await tools.ppa_top(year=2024, obec="Komárno", kind="anonymised")
    assert [r["name"] for r in res["ranking"]] == ["654321"]

    res = await tools.ppa_top(measure="VI", year=2024)
    assert res["ranking"][0]["total"] == 1000
    with pytest.raises(Exception, match="Unknown measure"):
        await tools.ppa_top(measure="XX.9")
