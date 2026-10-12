"""RÚZ tests. Templates in fixtures/ are real RÚZ templates (public form layouts); every entity,
statement, report and amount here is fictional, with record shapes copied from real API responses."""

import json
from pathlib import Path

import httpx
import pytest

from sk_public_money.http import SourceUnavailable

from sk_public_money.sources.ruz import client as client_mod
from sk_public_money.sources.ruz import parse, tools

FIXTURES = Path(__file__).parent / "fixtures"
# Value columns per table, as in real reports of each template.
WIDTHS = {699: [4, 2, 2], 687: [2, 2, 2], 1164: [2, 2, 2, 2], 716: [1, 1, 2, 2], 1180: [4, 2, 4, 4]}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def template(template_id: int) -> dict:
    return json.loads((FIXTURES / f"sablona_{template_id}.json").read_text())


def report(report_id: int, template_id: int, values: dict[tuple[int, int], list[str]], **extra) -> dict:
    """A report of template_id with values[(table index, row number)] = that row's cells, the rest blank."""
    tpl = template(template_id)
    tables = []
    for t, (table, width) in enumerate(zip(tpl["tabulky"], WIDTHS[template_id])):
        data = []
        for row in table["riadky"]:
            cells = values.get((t, row["cisloRiadku"]), [""] * width)
            assert len(cells) == width
            data += cells
        tables.append({"nazov": table["nazov"], "data": data})
    return {
        "id": report_id, "idUctovnejZavierky": 900, "idSablony": template_id, "pristupnostDat": "Verejné",
        "datumPoslednejUpravy": "2026-06-30", "zdrojDat": "FRSR", "kodDanovehoUradu": "123",
        "obsah": {"titulnaStrana": {"nazovUctovnejJednotky": "Fiktívna Firma s.r.o.", "ico": "01234567",
                                    "obdobieOd": "2025-01", "obdobieDo": "2025-12"}, "tabulky": tables},
        **extra,
    }


# A small company on the full template: current year in the third of four asset columns.
POD = report(1001, 699, {
    (0, 1): ["900", "100", "800", "750"],  # SPOLU MAJETOK: brutto, korekcia, netto, netto previous
    (1, 79): ["800", "750"],
    (1, 80): ["300", "280"],  # Vlastné imanie
    (1, 100): ["20", "15"],  # profit in the balance sheet: must not be taken as net_profit
    (2, 2): ["1500", "1400"],
    (2, 3): ["1000", "900"],  # tovar
    (2, 4): ["", ""],  # výrobky
    (2, 5): ["400", "380"],  # služby
    (2, 29): ["5", "4"],
    (2, 61): ["20", "15"],
})
MUJ = report(1002, 687, {
    (0, 1): ["5000", "4000"],
    (1, 25): ["-1200", "-800"],
    (2, 1): ["7000", "6000"],
    (2, 2): ["2500", "2000"],
    (2, 3): ["4400", "3900"],
    (2, 20): ["1", "0"],
    (2, 38): ["-400", "-300"],
})
# Single-entry non-profit: the current year is split into untaxed and taxed activity, with no total.
NO = report(1003, 1164, {
    (0, 13): ["100.5", "200"],
    (0, 16): ["1000", "250.25"],
    (1, 26): ["30", "-10.5"],
    (2, 11): ["800", "700"],
    (3, 17): ["650", "600"],
})
# Sole trader: the balance tables list the previous year first.
FO = report(1004, 716, {
    (0, 1): ["300"],
    (0, 2): ["1200"],
    (0, 4): ["1600"],
    (1, 12): ["450"],
    (2, 15): ["999", "2000"],  # previous, current
    (3, 21): ["900", "1800"],
})
IFRS = {
    "id": 1005, "idUctovnejZavierky": 902, "idSablony": 709, "pristupnostDat": "Verejné prílohy",
    "prilohy": [{"id": 77, "meno": "Účtovná závierka IFRS.PDF", "mimeType": "application/pdf", "pocetStran": 0,
                 "velkostPrilohy": 2068366, "jazyk": "SK", "digest": "0" * 64}],
    "datumPoslednejUpravy": "2026-07-05", "zdrojDat": "FRSR",
}


def test_pod_takes_netto_of_current_year_and_sums_sales():
    figures, not_mapped = parse.key_figures([(POD, template(699))])

    assert not_mapped == {}
    assert figures["total_assets"]["value"] == 800
    assert figures["total_assets"]["column"] == "Bežné účtovné obdobie / Netto 2"
    assert figures["total_assets"]["rows"] == ["Strana aktív, r. 1: SPOLU MAJETOK r. 02 + r. 33 + r. 74"]
    assert figures["equity"]["value"] == 300
    assert figures["revenue"]["value"] == 1400
    assert len(figures["revenue"]["rows"]) == 3
    assert figures["total_income"]["value"] == 1505
    assert figures["net_profit"]["value"] == 20
    assert figures["net_profit"]["rows"][0].startswith("Výkaz ziskov a strát, r. 61:")


def test_micro_entity():
    figures, not_mapped = parse.key_figures([(MUJ, template(687))])

    assert not_mapped == {}
    assert {k: v["value"] for k, v in figures.items()} == {
        "revenue": 6900, "total_income": 7001, "net_profit": -400, "total_assets": 5000, "equity": -1200,
    }


def test_single_entry_non_profit_sums_activity_columns():
    figures, not_mapped = parse.key_figures([(NO, template(1164))])

    assert not_mapped == {}
    assert figures["total_income"]["value"] == 1250.25
    assert figures["total_income"]["column"] == "sum of Nezdaňovaná činnosť + Zdaňovaná činnosť"
    assert figures["revenue"]["value"] == 300.5
    assert figures["net_profit"]["value"] == 19.5
    assert figures["total_assets"]["value"] == 800
    assert figures["total_assets"]["column"] == "Účtovné obdobie"
    assert figures["equity"]["value"] == 650
    assert "no equity" in figures["equity"]["note"]


def test_sole_trader_reads_current_year_although_it_comes_second():
    figures, _ = parse.key_figures([(FO, template(716))])

    assert figures["total_assets"]["value"] == 2000
    assert figures["total_assets"]["column"] == "Za bežné účtovné obdobie"
    assert figures["equity"]["value"] == 1800
    assert figures["revenue"]["value"] == 1500
    assert figures["net_profit"]["value"] == 450


def test_double_entry_non_profit_takes_total_of_activities():
    nuj = report(1006, 1180, {
        (0, 60): ["1000", "200", "800", "700"],
        (1, 61): ["500", "450"],
        (3, 39): ["10", "5", "15", "12"],
        (3, 74): ["90", "20", "110", "100"],
        (3, 78): ["7", "3", "10", "8"],
    })
    figures, not_mapped = parse.key_figures([(nuj, template(1180))])

    assert not_mapped == {}
    assert figures["net_profit"] == {
        "value": 10, "column": "Činnosť / Spolu",
        "rows": ["Výnosy, r. 78: Výsledok hospodárenia po zdanení (r. 75 - (r. 76 + r. 77)) (+/-)"],
    }
    assert figures["total_assets"]["value"] == 800
    assert figures["total_income"]["value"] == 110


def test_blank_and_unmapped_figures_are_reported():
    figures, not_mapped = parse.key_figures([])
    assert figures == {}
    assert not_mapped["revenue"] == "no report of this statement has structured data"

    blank = report(1007, 687, {})
    figures, _ = parse.key_figures([(blank, template(687))])
    assert figures["equity"]["value"] is None
    assert "blank" in figures["equity"]


def test_mismatched_template_is_not_read():
    figures, not_mapped = parse.key_figures([(MUJ, template(699))])

    assert "total_assets" not in figures
    assert "table 'Strana aktív' has 46 values for 78 rows" in not_mapped["_problems"]


def test_ico_normalization():
    assert parse.ico(" 3 1322 832 ") == "31322832"
    assert parse.ico("3328") == "00003328"
    with pytest.raises(ValueError):
        parse.ico("SK123")


# --- tools against a mocked API --------------------------------------------------------------

ENTITY = {
    "id": 500, "idUctovnychZavierok": [901, 900, 903, 902], "idVyrocnychSprav": [950],
    "skNace": "62010", "konsolidovana": True, "ico": "01234567", "dic": "2020000000",
    "nazovUJ": "Fiktívna Firma s.r.o.", "mesto": "Nitra", "ulica": "Hlavná 12", "psc": "94901",
    "datumPoslednejUpravy": "2026-07-03", "datumZalozenia": "2005-04-01", "pravnaForma": "112",
    "velkostOrganizacie": "05", "druhVlastnictva": "2", "kraj": "SK023", "okres": "SK0231",
    "sidlo": "SK0231500011", "zdrojDat": "SUSR",
}
STATEMENTS = {
    900: {"id": 900, "idUJ": 500, "idUctovnychVykazov": [1002], "typ": "Riadna", "obdobieOd": "2025-01",
          "obdobieDo": "2025-12", "datumPodania": "2026-06-30", "datumZostaveniaK": "2025-12-31", "zdrojDat": "FRSR"},
    901: {"id": 901, "idUJ": 500, "idUctovnychVykazov": [1001], "typ": "Riadna", "obdobieOd": "2024-01",
          "obdobieDo": "2024-12", "datumPodania": "2025-06-30", "zdrojDat": "FRSR"},
    902: {"id": 902, "idUJ": 500, "idUctovnychVykazov": [1005], "typ": "Riadna", "konsolidovana": True,
          "obdobieOd": "2025-01", "obdobieDo": "2025-12", "datumPodania": "2026-06-30", "zdrojDat": "FRSR"},
    903: {"id": 903, "stav": "ZMAZANÉ", "datumPoslednejUpravy": "2024-05-31"},
}
REPORTS = {1001: POD, 1002: MUJ, 1005: IFRS}
CODEBOOKS = {
    "pravne-formy": {"klasifikacie": [{"kod": "112", "nazov": {"sk": "Spol. s r. o."}}]},
    "velkosti-organizacie": {"klasifikacie": [{"kod": "05", "nazov": {"sk": "5-9 zamestnancov"}}]},
    "druhy-vlastnictva": {"klasifikacie": [{"kod": "2", "nazov": {"sk": "Súkromné tuzemské"}}]},
    "sk-nace": {"klasifikacie": [{"kod": "62010", "nazov": {"sk": "Počítačové programovanie"}}]},
}


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Route requests to canned responses; records every request path and query."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/cruz-public/")
        params = dict(request.url.params)
        seen.append((path, params))
        if path == "api/uctovne-jednotky":
            ids = [500, 501] if params.get("ico") == "01234567" else []
            return httpx.Response(200, json={"id": ids, "existujeDalsieId": False})
        if path == "api/uctovna-jednotka":
            if params["id"] == "500":
                return httpx.Response(200, json=ENTITY)
            if params["id"] == "501":
                return httpx.Response(200, json={"id": 501, "stav": "ZMAZANÉ", "datumPoslednejUpravy": "2024-05-31"})
            return httpx.Response(404)
        if path == "api/uctovna-zavierka":
            return httpx.Response(200, json=STATEMENTS[int(params["id"])])
        if path == "api/uctovny-vykaz":
            return httpx.Response(200, json=REPORTS[int(params["id"])])
        if path == "api/sablona":
            tid = int(params["id"])
            return httpx.Response(200, json=template(tid) if tid != 709 else {"id": 709, "nazov": "IFRS účtovná závierka"})
        if path.removeprefix("api/") in CODEBOOKS:
            return httpx.Response(200, json=CODEBOOKS[path.removeprefix("api/")])
        if path == "domain/suggestion/search":
            return httpx.Response(200, json=[
                {"id": "500", "entNumber": "01234567", "taxNumber": "2020000000", "entityName": "<b>Fiktívna</b> Firma s.r.o."},
                {"id": "600", "entNumber": "", "taxNumber": "", "entityName": "Občianske združenie <b>Fiktívne</b>"},
            ])
        return httpx.Response(400, text=f"unexpected {path} {params}")

    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(tools, "_client", client_mod.RUZClient(http=http, cache=tmp_path))
    return seen


@pytest.mark.anyio
async def test_get_entity_lists_statements_newest_first(api):
    out = await tools.ruz_get_entity(ico="123 4567")

    assert ("api/uctovne-jednotky", {"zmenene-od": "2000-01-01", "ico": "01234567"}) in api
    [entity] = out["entities"]  # the deleted duplicate record is skipped
    assert entity["name"] == "Fiktívna Firma s.r.o."
    assert entity["url"] == "https://www.registeruz.sk/cruz-public/domain/accountingentity/show/500"
    assert entity["address"] == "Hlavná 12, 94901 Nitra"
    assert entity["legal_form"] == "Spol. s r. o."
    assert entity["employees"] == "5-9 zamestnancov"
    assert entity["sk_nace"] == "62010 Počítačové programovanie"
    assert [(s["statement_id"], s["year"], s["kind"]) for s in entity["statements"]] == [
        (902, 2025, "consolidated"), (900, 2025, "individual"), (901, 2024, "individual"),
    ]
    assert entity["annual_reports"] == 1


@pytest.mark.anyio
async def test_get_financials(api):
    out = await tools.ruz_get_financials(ico="01234567")

    [entity] = out["entities"]
    assert [s["statement_id"] for s in entity["statements"]] == [900, 901]
    latest = entity["statements"][0]
    assert latest["year"] == 2025
    assert latest["figures"]["net_profit"]["value"] == -400
    assert latest["documents"] == [{
        "report_id": 1002, "template": "Úč MUJ", "access": "public",
        "pdf": "https://www.registeruz.sk/cruz-public/domain/financialreport/pdf/1002",
    }]
    assert "not_mapped" not in latest

    # Templates are cached on disk: asking again fetches no template.
    api.clear()
    out = await tools.ruz_get_financials(ico="01234567", years=[2024])
    assert [s["year"] for s in out["entities"][0]["statements"]] == [2024]
    assert not [p for p, _ in api if p == "api/sablona"]


@pytest.mark.anyio
async def test_get_financials_ifrs_only_links_documents(api):
    out = await tools.ruz_get_financials(ruz_id=500, consolidated=True)

    [st] = out["entities"][0]["statements"]
    assert st["figures"] == {}
    assert st["not_mapped"]["net_profit"] == "no report of this statement has structured data"
    assert st["documents"] == [{
        "report_id": 1005, "template": "IFRS účtovná závierka", "access": "attachments only",
        "attachments": [{"name": "Účtovná závierka IFRS.PDF",
                         "url": "https://www.registeruz.sk/cruz-public/domain/financialreport/attachment/77"}],
    }]


@pytest.mark.anyio
async def test_unknown_entity(api):
    with pytest.raises(LookupError, match="No accounting entity with IČO 07654321"):
        await tools.ruz_get_entity.__wrapped__(ico="7654321")
    with pytest.raises(LookupError):
        await tools.ruz_get_entity.__wrapped__(ruz_id=999)
    with pytest.raises(ValueError, match="exactly one"):
        await tools.ruz_get_financials.__wrapped__()


@pytest.mark.anyio
async def test_search_strips_highlighting(api):
    out = await tools.ruz_search_entity(query="  fiktivna   firma ")

    assert ("domain/suggestion/search", {"query": "fiktivna firma"}) in api
    assert out == {
        "results": [
            {"ruz_id": 500, "name": "Fiktívna Firma s.r.o.", "ico": "01234567", "dic": "2020000000"},
            {"ruz_id": 600, "name": "Občianske združenie Fiktívne", "ico": None, "dic": None},
        ],
        "has_more": False,
    }


@pytest.mark.anyio
async def test_blocked_portal_is_reported_as_unavailable(monkeypatch, tmp_path):
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    monkeypatch.setattr(tools, "_client", client_mod.RUZClient(http=http, cache=tmp_path))

    with pytest.raises(SourceUnavailable, match="HTTP 403"):
        await tools.ruz_search_entity.__wrapped__(query="fiktivna")


def test_user_agent_has_no_apostrophe():
    # registeruz.sk's firewall rejects one on its web paths.
    assert "'" not in client_mod.RUZClient()._http.headers["User-Agent"]
