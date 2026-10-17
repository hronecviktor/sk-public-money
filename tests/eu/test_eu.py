"""ITMS21+ tests against a mock of api.itms21.sk/public/v1. All organisations, people and amounts are
fictional; the record shapes are copied from real responses (linkApi lists, inlined codelists, epoch
milliseconds, "result" instead of "results" for procurements)."""

import io
import json

import httpx
import pytest

from sk_public_money.sources.eu import client as client_mod
from sk_public_money.sources.eu import index as index_mod
from sk_public_money.sources.eu import parse, sync, tools

SK = {"id": 210, "kod": "1012SK", "kodZdroj": "703", "nazovSk": "Slovensko", "nazovEn": "Slovakia", "lokalizacia": "SLOVENSKA_REPUBLIKA"}
MS_2025_03_01 = 1740783600000  # 2025-03-01 00:00 Bratislava
MS_2026_04_09 = 1775685600000  # 2026-04-09 00:00 Bratislava


@pytest.fixture
def anyio_backend():
    return "asyncio"


def link(kind, i):
    return {"id": i, "href": f"/{kind}/id/{i}"}


def nuts(code, name):
    return {"id": 16, "kod": f"1006{code}", "kodZdroj": code, "nazovSk": name, "nazovEn": name, "platnostOd": 725842800000}


def subject(i, name, ico, form="Spoločnosť s ručením obmedzeným", **extra):
    return {
        "id": i, "href": f"/subjekt/id/{i}", "nazov": name, "ico": ico, "dic": "2020000000", "platitelDph": False,
        "pravnaForma": {"id": 22, "kod": "1009112", "kodZdroj": "112", "nazovSk": form, "platnostOd": 662684400000, "statPlatnosti": SK},
        "adresa": {"ulica": "Hlavná", "cislo": "1", "psc": "99901", "obec": "Fiktívna Lehota", "stat": SK},
        **extra,
    }


SUBJECTS = [
    subject(100063, "Obec Fiktívna Lehota", "00999901", "Obec (obecný úrad), mesto (mestský úrad)"),
    subject(100064, "Ministerstvo príkladov SR", "00999902", "Rozpočtová organizácia"),
    subject(100200, "Ján Šíroký - STAVBY", "4567890", "Podnikateľ-fyzická osoba-nezapísaný v obchodnom registri"),
    subject(100300, "Vzorové združenie", "12 345 678", "Občianske združenie"),
]
PROGRAMMES = [
    {"id": 1, "href": "/program/id/1", "kod": "401000", "nazovSk": "Program Slovensko - SK - EFRR/KF/FST/ESF+", "skratka": "PSK"},
    {"id": 6, "href": "/program/id/6", "kod": "408010", "nazovSk": "Fond pre azyl, migráciu a integráciu", "skratka": "AMIF"},
]
CALLS = [{"id": 3547, "href": "/vyzva/id/3547", "kod": "PSK-MIRRI-501-2024", "nazovSk": "Komunitný život", "program": link("program", 1)}]


def project(i, code, name, recipient, amount, partners=(), application=None, call=3547):
    return {
        "id": i, "href": f"/projekt/id/{i}", "kod": code, "nazov": name, "vyzva": link("vyzva", call),
        "miestoRealizacie": [nuts("SK042", "Košický kraj")], "obsahujeMiestoRealizacieZahranicie": False,
        "prijimatel": link("subjekt", recipient), "partner": [link("subjekt", p) for p in partners],
        "vrealizacii": True, "ukonceny": False, "zonfp": link("zonfp", application) if application else None,
        "vylucenyZFinancovania": False, "stav": "Zmluva uzavretá",
        "zmluvaProjekt": {"id": 105453, "cislo": "1653/2025", "datumPlatnosti": MS_2025_03_01, "datumUcinnosti": MS_2025_03_01,
                          "url": "https://www.crz.gov.sk/zmluva/99000001/", "dokument": []},
        "skutocnaRealizaciaZaciatok": MS_2025_03_01, "celkovaZazmluvnenaSuma": amount, "zameranieProjektu": "IUI_21",
        "jeOtvorenaZmena": False, "predchodca": [], "createdAt": MS_2025_03_01, "updatedAt": MS_2026_04_09,
    }


PROJECTS = [
    project(23889, "401501F545", "Prestavba kultúrneho domu v obci Fiktívna Lehota", 100063, 352847.0, partners=[100300], application=205668),
    project(25682, "408010I909", "Integrácia cudzincov", 100064, 4696162.48, call=9999),
]


def application(i, code, name, applicant, requested, approved=None, status="Zmluva uzavretá"):
    return {
        "id": i, "href": f"/zonfp/id/{i}", "kod": code, "nazov": name, "vyzva": link("vyzva", 3547),
        "miestoRealizacie": [nuts("SK042", "Košický kraj")], "ziadatel": link("subjekt", applicant), "partner": [],
        "schvalena": approved is not None, "neschvalena": approved is None, "stav": status, "datumVytvorenia": MS_2025_03_01,
        "sumaZiadanaNFP": requested, "sumaZiadanaCelkova": requested, "sumaSchvalenaNFP": approved, "sumaSchvalenaCelkova": approved,
        "zameranieProjektu": "IUI_21", "datumPredlozenia": MS_2025_03_01, "predchodca": [], "createdAt": MS_2025_03_01,
    }


APPLICATIONS = [
    application(205668, "NFP401501F545", "Prestavba kultúrneho domu v obci Fiktívna Lehota", 100063, 335204.65, 335204.65),
    application(205700, "NFP401501F999", "Šírenie kultúry v Lehote", 100200, 120000.0, None, "Neschválená"),
]


def claim(i, project_id, amount, paid):
    return {
        "id": i, "href": f"/zop/id/{i}", "createdAt": MS_2026_04_09, "updatedAt": MS_2026_04_09, "kod": f"401501F5455002{i % 100:02d}",
        "narokovanaSuma": amount, "predkladanaZa": link("subjekt", 100063), "prijimatel": link("subjekt", 100063),
        "projekt": link("projekt", project_id), "typ": "REFUNDACIA", "zopPredlozenaZaViacSubjektov": False, "neuhradena": not paid,
    }


CLAIMS = [claim(189701, 23889, 67074.36, True), claim(189730, 23889, 10000.0, False)]
PROCUREMENTS = [
    {"id": 106502, "href": "/verejneobstaravanie/id/106502", "kod": "VO63750030", "nazov": "Prestavba kultúrneho domu",
     "obstaravatelSubjekt": link("subjekt", 100063),
     "druhObstaravatelaVO": {"id": 200001, "kod": "104610", "nazovSk": "Verejný obstarávateľ podľa § 7 ods. 1 písm. b) zákona č. 343/2015 Z. z. - obec"},
     "nasledniciVO": [], "predpokladanaHodnotaZakazky": 288138.09, "stav": "Ukončené", "createdAt": MS_2025_03_01, "updatedAt": MS_2026_04_09},
    {"id": 106600, "href": "/verejneobstaravanie/id/106600", "kod": "VO63750099", "nazov": "Tlmočnícke služby",
     "obstaravatelSubjekt": link("subjekt", 100064), "nasledniciVO": [], "predpokladanaHodnotaZakazky": 50000.0,
     "stav": "Pripravované", "createdAt": MS_2025_03_01, "updatedAt": MS_2026_04_09},
]
PROCUREMENT_DETAIL = {
    **PROCUREMENTS[0], "obstaravatelSubjekt": subject(100063, "Obec Fiktívna Lehota", "00999901"),
    "druhZakazky": {"id": 3, "kod": "104803", "nazovSk": "Stavebné práce"},
    "hlavnyPredmetHlavnySlovnik": {"id": 9, "kod": "104945000000-7", "nazovSk": "Stavebné práce"},
    "metodaVO": {"id": 2, "kod": "1041mvo_2", "nazovSk": "Podlimitná zákazka"},
    "postupObstaravania": {"id": 4, "kod": "1042104772", "nazovSk": "Podlimitná zákazka bez zverejnenia podľa § 108 ZVO"},
    "kriteriumNaVyhodnoteniePonuk": "NAJNIZSIA_CENA", "lehotaNaPredkladaniePonuk": MS_2025_03_01,
    "pocetPrijatychPonuk": 2, "pocetVylucenychPonuk": 0, "programy": [PROGRAMMES[0]],
    "projekty": [{"id": 23889, "href": "/projekt/id/23889", "kod": "401501F545", "nazov": "Prestavba kultúrneho domu"}],
    "trvanieZakazkyHodnota": 2.0, "trvanieZakazkyMernaJednotka": "MESIAC", "uctovneDoklady": [],
    "zverejneneVoVestnikuEU": False,
    "zmluvyVO": [{"id": 69627, "href": "/zmluvaverejneobstaravanie/id/69627", "kod": "VO63750030001", "nazov": "Zmluva o dielo",
                  "cisloZmluvy": "2/4/2026", "datumUcinnosti": MS_2026_04_09,
                  "hlavnyDodavatelDodavatelObstaravatel": link("dodavatelobstaravatel", 17614), "celkovaSumaZmluvy": 323367.0,
                  "createdAt": MS_2026_04_09}],
}
SUPPLIERS = {17614: {"id": 17614, "href": "/dodavatelobstaravatel/id/17614", "nazov": "Fiktívna Stavebná s.r.o.", "ico": "87654321",
                     "dic": "2022000000", "platitelDph": True, "adresa": {"obec": "Trnava", "psc": "91708", "stat": SK}}}
INVOICES = [
    {"id": 488691, "href": "/uctovnydoklad/id/488691", "nazov": "Paušálna sadzba", "cisloDokladu": "907_2", "celkovaVyskaDokladu": 4388.04,
     "typ": "INTERNY", "vlastnikDokladu": link("subjekt", 100063), "dodavatelSubjekt": link("subjekt", 100063), "createdAt": MS_2026_04_09},
    {"id": 488695, "href": "/uctovnydoklad/id/488695", "nazov": "Faktúra za stavebné práce", "cisloDokladu": "260002 ",
     "celkovaVyskaDokladu": 25835.0, "typ": "EXTERNY", "vlastnikDokladu": link("subjekt", 100063),
     "dodavatelDodavatelObstaravatel": link("dodavatelobstaravatel", 17614), "createdAt": MS_2026_04_09},
    {"id": 488696, "href": "/uctovnydoklad/id/488696", "nazov": "Faktúra", "cisloDokladu": "260003",
     "celkovaVyskaDokladu": 1000.0, "typ": "EXTERNY", "vlastnikDokladu": link("subjekt", 100063),
     "dodavatelDodavatelObstaravatel": link("dodavatelobstaravatel", 17614), "createdAt": MS_2026_04_09},
]
PROJECT_DETAIL = {
    **PROJECTS[0], "program": link("program", 1), "poskytovatelSubjekt": link("subjekt", 100064),
    "poskytovatelOrgan": {"id": 376, "kod": "21_RO_PSK", "nazov": "Riadiaci orgán PSK"},
    "zazmluvnenaSumaNfp": 324619.24, "poskytnuteProstriedky": 68712.46,
    "financnyPlan": [{"zdroj": {"id": 200019, "kod": "10521BA1", "nazovSk": "Európsky fond regionálneho rozvoja – prostriedky EÚ"}, "suma": 299919.95}],
    "popis": "<div>Predmetom projektu je <b>prestavba</b>&nbsp;kultúrneho domu.</div>",
    "miestoRealizacieFull": [{"nuts3": nuts("SK042", "Košický kraj"), "nuts4": nuts("SK0426", "Košice - okolie"), "nuts5": nuts("SK0426999999", "Fiktívna Lehota")}],
    "ukazovatelVystupu": [{"ukazovatelProjektovySC": {"id": 822, "ukazovatelProjektovy": {"id": 1704, "nazovSk": "Podlahová plocha", "mernaJednotka": {"kod": "105318", "nazovSk": "m2"}}}, "cielovaHodnotaSpolu": 337.3}],
    "zmenaProjekt": [{"id": 579454, "predmet": "<p>zmena cien položiek po VO</p>", "datumUcinnosti": MS_2026_04_09}],
    "dodavatel": [{"nazov": "Fiktívna Stavebná s.r.o.", "ico": "87654321", "dic": "2022000000"}],
    "aktivity": [{"id": 1, "nazov": "Prestavba kultúrneho domu "}],
}
LISTS = {"subjekt": SUBJECTS, "vyzva": CALLS, "projekt": PROJECTS, "zonfp": APPLICATIONS, "zop": CLAIMS,
         "verejneobstaravanie": PROCUREMENTS, "uctovnydoklad": INVOICES}


def handler(calls: list):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/public/v1/")
        params = dict(request.url.params)
        calls.append((path, params))
        if path == "program":
            return httpx.Response(200, json={"offset": 0, "limit": -1, "size": len(PROGRAMMES), "results": PROGRAMMES})
        if path == "uctovnydoklad" and "projektId" in params:
            return httpx.Response(200, json={"offset": 0, "limit": -1, "size": 3, "results": INVOICES})
        if path in LISTS:
            if path == "subjekt":  # sorted by name whatever the parameters say, like the real API
                rows = sorted(LISTS[path], key=lambda r: r["nazov"])
            else:
                rows = sorted(LISTS[path], key=lambda r: r["id"])
                rows = [r for r in rows if r["id"] >= int(params.get("minId", 0))]  # inclusive, like the real API
            start = int(params.get("offset", 0))
            page = rows[start : start + int(params["limit"])]
            key = "result" if path == "verejneobstaravanie" else "results"
            return httpx.Response(200, json={"offset": 0, "limit": int(params["limit"]), "size": len(rows), key: page})
        if path == "verejneobstaravanie/id/106502":
            return httpx.Response(200, json=PROCUREMENT_DETAIL)
        if path == "verejneobstaravanie/id/106600":
            return httpx.Response(200, json={**PROCUREMENTS[1], "zmluvyVO": [], "projekty": []})
        if path.startswith("dodavatelobstaravatel/id/") and int(path.rsplit("/", 1)[1]) in SUPPLIERS:
            return httpx.Response(200, json=SUPPLIERS[int(path.rsplit("/", 1)[1])])
        if path in ("projekt/id/23889", "projekt/401501F545"):
            return httpx.Response(200, json=PROJECT_DETAIL)
        if path.startswith(("projekt/", "dodavatelobstaravatel/", "subjekt/id/")):
            return httpx.Response(404, json={"status": 404, "message": "HTTP 404 Not Found"})
        # Unknown paths: 403 or 200, both with only a support ID.
        return httpx.Response(200, json={"supportID": "1300126470"})

    return handle


def make_client(calls):
    c = client_mod.ITMSClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler(calls))), interval=0)
    c.page = 2  # exercise paging
    return c


@pytest.fixture
async def built(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    calls = []
    ok = await sync.run(out=io.StringIO(), client=make_client(calls))
    assert ok
    monkeypatch.setattr(tools, "_index", None)
    monkeypatch.setattr(tools, "_client", make_client(calls))
    return calls


def test_parse_dates_and_ico():
    assert parse.date(MS_2026_04_09) == "2026-04-09"
    assert parse.norm_ico("4567890") == "04567890"
    assert parse.norm_ico("12 345 678") == "12345678"
    org = parse.organisation(SUBJECTS[2])
    assert org["name"] == "Ján Šíroký - STAVBY" and org["ico"] == "04567890"
    assert org["address"] == "Hlavná 1, 99901 Fiktívna Lehota"


def test_procurement_detail_parse():
    d = parse.procurement_detail(PROCUREMENT_DETAIL)
    assert d["cpv"] == "45000000-7 Stavebné práce"
    assert d["duration"] == "2 mesiac"
    assert d["project_ids"] == [23889]
    assert d["contracts"][0] == {"id": 69627, "procurement_id": 106502, "code": "VO63750030001", "name": "Zmluva o dielo",
                                 "number": "2/4/2026", "effective": "2026-04-09", "supplier_id": 17614, "amount": 323367.0}


@pytest.mark.anyio
async def test_sync_pages_by_min_id_or_offset(built):
    # 2 projects in pages of 2: minId 0, then the last ID + 1 (minId is inclusive), an empty page.
    assert [p["minId"] for path, p in built if path == "projekt"] == ["0", "25683"]
    # Subjects come sorted by name, so they are paged by offset.
    assert [p.get("offset", p.get("minId")) for path, p in built if path == "subjekt"] == ["0", "2", "4"]
    ix = index_mod.EUIndex()
    c = ix.counts()
    assert c["projects"] == 2 and c["applications"] == 2 and c["procurements_with_detail"] == 2
    assert c["suppliers"] == 1 and c["contracts"] == 1 and c["invoices"] == 3


@pytest.mark.anyio
async def test_list_missing_records_is_not_stored(tmp_path):
    class Short(client_mod.ITMSClient):
        async def get_json(self, path, params=None):
            data = await super().get_json(path, params)
            if path == "zop":
                data["size"] = 50
            return data

    ix = index_mod.EUIndex(tmp_path / "x.sqlite")
    ok = await sync.run(out=io.StringIO(), index=ix, client=Short(http=httpx.AsyncClient(transport=httpx.MockTransport(handler([]))), interval=0))
    assert not ok


@pytest.mark.anyio
async def test_search_projects_by_ico(built):
    res = await tools.eu_search_projects(ico="00999901")
    assert res["total"] == 1
    p = res["results"][0]
    assert p["code"] == "401501F545" and p["matched_as"] == ["recipient"]
    assert p["programme"].startswith("PSK")
    assert p["recipient"] == {"name": "Obec Fiktívna Lehota", "ico": "00999901"}
    assert p["partners"] == [{"name": "Vzorové združenie", "ico": "12345678"}]
    assert p["payment_claims"] == {"count": 2, "claimed": 77074.36, "claimed_in_paid_claims": 67074.36, "paid_claims": 1}

    partner = await tools.eu_search_projects(ico="12 345 678", role="partner")
    assert [r["id"] for r in partner["results"]] == [23889]
    assert (await tools.eu_search_projects(ico="12345678", role="recipient"))["total"] == 0


@pytest.mark.anyio
async def test_search_projects_programme_from_code_and_text(built):
    # Call 9999 is not in the call list: the programme comes from the project code.
    amif = await tools.eu_search_projects(programme="amif")
    assert [r["code"] for r in amif["results"]] == ["408010I909"]
    # Diacritics and endings of longer words: "kulturny domu" finds "kultúrneho domu".
    assert (await tools.eu_search_projects(text="kulturny domu"))["total"] == 1
    assert (await tools.eu_search_projects(programme="Program Slovensko"))["total"] == 1
    with pytest.raises(Exception, match="Unknown or ambiguous programme"):
        await tools.eu_search_projects(programme="XYZ")


@pytest.mark.anyio
async def test_search_applications(built):
    res = await tools.eu_search_projects(ico="4567890", kind="all")
    assert [(r["kind"], r["status"]) for r in res["results"]] == [("application", "Neschválená")]
    # The approved application is shown as its project.
    allk = await tools.eu_search_projects(kind="all", text="Lehota")
    assert sorted(r["kind"] for r in allk["results"]) == ["application", "project"]
    assert (await tools.eu_search_projects(text="Siroky", kind="applications"))["total"] == 1


@pytest.mark.anyio
async def test_unknown_ico(built):
    res = await tools.eu_search_projects(ico="99999999")
    assert res["total"] == 0 and "2021-2027" in res["note"]


@pytest.mark.anyio
async def test_search_procurements_as_supplier_and_buyer(built):
    res = await tools.eu_search_procurements(ico="87654321", role="supplier")
    assert res["total"] == 1
    p = res["results"][0]
    assert p["buyer"] == {"name": "Obec Fiktívna Lehota", "ico": "00999901"}
    assert p["contracts"][0]["main_supplier"] == {"name": "Fiktívna Stavebná s.r.o.", "ico": "87654321"}
    assert p["projects"] == [{"id": 23889, "code": "401501F545", "name": "Prestavba kultúrneho domu v obci Fiktívna Lehota"}]
    assert p["tenders_received"] == 2
    inv = res["invoices_as_supplier"]
    assert inv["count"] == 2 and inv["total"] == 26835.0
    assert inv["by_recipient"][0]["recipient"]["ico"] == "00999901"

    buyer = await tools.eu_search_procurements(ico="00999902", role="buyer")
    assert [r["code"] for r in buyer["results"]] == ["VO63750099"]
    assert (await tools.eu_search_procurements(ico="87654321", role="buyer"))["total"] == 0
    # Supplier names are in the full text too.
    assert (await tools.eu_search_procurements(text="fiktivna stavebna"))["total"] == 1
    assert (await tools.eu_search_procurements(project="401501F545"))["total"] == 1


@pytest.mark.anyio
async def test_get_project(built):
    res = await tools.eu_get_project(project="401501f545")
    assert res["recipient"]["ico"] == "00999901"
    assert res["provider"]["name"] == "Ministerstvo príkladov SR"
    assert res["amounts"]["paid_to_recipient"] == 68712.46
    assert res["contract"]["url"] == "https://www.crz.gov.sk/zmluva/99000001/"
    assert res["description"] == "Predmetom projektu je prestavba kultúrneho domu."
    assert res["places"] == ["Fiktívna Lehota, Košice - okolie, Košický kraj"]
    assert res["output_indicators"] == [{"name": "Podlahová plocha", "unit": "m2", "target": 337.3}]
    assert res["invoices"]["by_supplier"] == [{"supplier": {"name": "Fiktívna Stavebná s.r.o.", "ico": "87654321"}, "invoices": 2, "total": 26835.0}]
    assert res["invoices"]["internal_documents"] == {"count": 1, "total": 4388.04}
    assert res["procurements"][0]["contracts"][0]["number"] == "2/4/2026"
    assert res["payment_claims"]["count"] == 2


@pytest.mark.anyio
async def test_get_project_without_index(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tools, "_index", None)
    calls = []
    monkeypatch.setattr(tools, "_client", make_client(calls))
    res = await tools.eu_get_project(project="23889")
    # Organisations are fetched live; the mock knows no subjekt details.
    assert res["recipient"] == {"subject_id": 100063}
    assert "not been built" in res["note"]
    with pytest.raises(Exception, match="not been built"):
        await tools.eu_search_projects(ico="00999901")


@pytest.mark.anyio
async def test_errors(built):
    with pytest.raises(Exception, match="Not found"):
        await tools.eu_get_project(project="1")
    with pytest.raises(Exception, match="8 digits"):
        await tools.eu_search_procurements(ico="123456789")
    c = make_client([])
    with pytest.raises(Exception, match="refused"):
        await c.get_json("nonsense")


@pytest.mark.anyio
async def test_details_refresh_only_when_due(built):
    ix = index_mod.EUIndex()
    assert ix.procurements_due(open_age=sync.OPEN_AGE, closed_age=sync.CLOSED_AGE, final=sync.FINAL) == []
    ix.replace_procurements([{**parse.procurement_row(PROCUREMENTS[0]), "updated_ms": MS_2026_04_09 + 1}, parse.procurement_row(PROCUREMENTS[1])])
    assert [i for i, _ in ix.procurements_due(open_age=sync.OPEN_AGE, closed_age=sync.CLOSED_AGE, final=sync.FINAL)] == [106502]
    # The detail fetched earlier is kept through a list reload.
    assert ix.procurement(106502)["contracts"]
    assert json.dumps(ix.procurement(106600))
