"""Court source tests against mocks of the ISU API and Súdna rada's list. All people and companies
here are fictional; record shapes are copied from real obcan.justice.sk and sudnarada.gov.sk responses."""

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sk_public_money.sources.court import client as client_mod
from sk_public_money.sources.court import parse, tools

API = "/pilot/api/ress-isu-service/v1/"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_pdf(text: str) -> bytes:
    """A one-page PDF with a text layer."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def hit(guid, day, snippet, *, court=("sud_901", "Okresný súd Vzorovo"), judge=("sudca_77", "JUDr. Jana Príkladná")):
    return {
        "guid": guid,
        "formaRozhodnutia": "Rozsudok",
        "sud": {"registreGuid": court[0], "nazov": court[1]},
        "sudca": {"registreGuid": judge[0], "meno": judge[1]},
        "identifikacneCislo": "1234567890",
        "spisovaZnacka": "5Cb/12/2024",
        "datumVydania": day,
        "zvyraznenie": [snippet],
    }


FACETS = [
    {"filterName": "sud_typ", "facetValueList": [{"text": "Okresný súd", "count": 2}]},
    {"filterName": "oblast_pravnej_upravy", "facetValueList": [{"text": "Obchodné právo", "count": 2}]},
    {"filterName": "sud_kraj", "facetValueList": [{"text": "Nitriansky kraj", "count": 2}]},
]
UNSPACED = [hit("a:1", "02.03.2025", 'žalobcu Fiktívna Rafinéria, a.s., IČO: <span class="highlight-substring">01234567</span>, proti')]
SPACED = [
    hit("b:2", "15.01.2026", 'IČO: <span class="highlight-substring">1 234 567</span> škodu'),
    UNSPACED[0],
]
DETAIL = {
    **hit("c:3", "30.06.2026", "x"),
    "povaha": ["Potvrdené"],
    "ecli": "ECLI:SK:OSVZ:2026:1234567890.3",
    "oblast": ["Obchodné právo"],
    "podOblast": ["Kúpna\xa0zmluva"],
    "odkazovanePredpisy": [{"nazov": "/SK/ZZ/1991/513", "url": "https://www.slov-lex.sk/pravne-predpisy/SK/ZZ/1991/513"}],
    "dokument": {"name": "Rozsudok.pdf", "fileExtension": "PDF", "size": 900, "url": "https://obcan.justice.sk/content/public/item/3"},
    "updateDate": "01.07.2026",
}
JUDGES = {
    "numFound": 1,
    "sudcaList": [{
        "registreGuid": "sudca_77", "meno": "JUDr. Jana PRÍKLADNÁ", "funkcia": "Sudca", "stav": "label.sudca.aktivny",
        "sud": {"registreGuid": "sud_901", "nazov": "Okresný súd Vzorovo"},
        "docasnePridelenySud": {"registreGuid": "sud_902", "nazov": "Krajský súd vo Vzorove"},
    }],
}


def declaration_item(name, court, year, href):
    return f"""<li class="mps-table__item border-bottom">
    <div class="mps-table__name">{name}   </div>
    <div class="mps-table__court">{court}</div>
    <div class="mps-table__att">
        <a href="{href}" class="direct" title="Stiahnuť Majetkové priznanie {year}">
<i class="fa fa-file-pdf-o" aria-hidden="true"></i><div>Majetkové priznanie {year} <span>(.pdf, 57.38 kB)</span></div></a>
    </div></li>"""


def declarations_page(items, total, current=1, pages=1):
    links = "".join(
        f'<li class="page-item"><a class="page-link" href="?page={p - 1}" aria-label="Stránka {p}">{p}</a></li>'
        if p != current else f'<li class="page-item active"><span class="page-link">{p}<span class="sr-only"> Aktuálna stránka {p}</span></span></li>'
        for p in range(1, pages + 1)
    )
    return f"""<html><body><div class="mps-table__records border-top">Počet záznamov: <b>{total}</b></div>
<ul class="mps-table border-top mb-4">{''.join(items)}</ul><ul class="pagination justify-content-center">{links}</ul></body></html>"""


DECLARATIONS = declarations_page([
    declaration_item("JUDr. Jana Príkladná", "Okresný súd Vzorovo", 2024, "/data/att/aaa/100.abc.pdf"),
    declaration_item("JUDr. Jana Príkladná", "Okresný súd Vzorovo", 2025, "/data/att/bbb/200.def.pdf"),
    declaration_item("Mgr. Peter Príkladný", "Krajský súd vo Vzorove", 2025, "/data/att/ccc/300.fed.pdf"),
], 3)


@pytest.fixture
def api(monkeypatch):
    """Route requests to canned responses; records (path, params) of each."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        seen.append((path, params))
        if request.url.host == "www.sudnarada.gov.sk":
            if path.startswith("/data/att/"):
                if path == "/data/att/bbb/200.def.pdf":
                    return httpx.Response(200, content=make_pdf("Majetkove priznanie sudcu za rok 2025"))
                return httpx.Response(200, text="<!DOCTYPE html><html>home page</html>")
            if params.get("nazov") == "prikladna":
                return httpx.Response(200, text=DECLARATIONS)
            return httpx.Response(200, text=declarations_page([], 0))
        if path == "/content/public/item/3":
            return httpx.Response(200, content=make_pdf("Okresny sud Vzorovo rozhodol"))
        route = path.removeprefix(API)
        if route == "rozhodnutie":
            query = params.get("query", "")
            if query.startswith("bad"):
                return httpx.Response(500, json={"message": "Error from server at http://solr:8983/solr: org.apache.solr.search.SyntaxError: Cannot parse"})
            if "ecli" in params:
                return httpx.Response(200, json={"numFound": 1, "rozhodnutieList": [hit("c:3", "30.06.2026", "x")]})
            if query == "01234567":
                return httpx.Response(200, json={"numFound": 1, "rozhodnutieList": UNSPACED, "filterList": FACETS})
            if query == '"1 234 567"':
                return httpx.Response(200, json={"numFound": 2, "rozhodnutieList": SPACED, "filterList": FACETS})
            return httpx.Response(200, json={"numFound": 41, "page": 0, "rozhodnutieList": SPACED, "filterList": FACETS})
        if route == "rozhodnutie/c:3":
            return httpx.Response(200, json=DETAIL)
        if route.startswith("rozhodnutie/"):
            return httpx.Response(404, json={"error": "Not Found", "message": f"Rozhodnutie does not exist. Id: {route[12:]}"})
        if route == "sud":
            courts = [
                {"registreGuid": "sud_901", "nazov": "Okresný súd Vzorovo"},
                {"registreGuid": "sud_903", "nazov": "Okresný súd Vzorovo - okolie"},
            ]
            return httpx.Response(200, json={"numFound": 2, "sudList": courts})
        if route == "sudca":
            return httpx.Response(200, json=JUDGES if "kladn" in params.get("query", "") else {"numFound": 0, "sudcaList": []})
        return httpx.Response(400, json={"message": f"unexpected {route}"})

    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    monkeypatch.setattr(tools, "_client", client_mod.CourtClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    return seen


@pytest.mark.parametrize("ico, forms", [
    ("31322832", ["31322832", '"31 322 832"']),
    ("31 322 832", ["31322832", '"31 322 832"']),
    ("604381", ["00604381", '"604 381"']),
    ("1234567", ["01234567", '"1 234 567"']),
])
def test_ico_forms(ico, forms):
    assert tools.ico_forms(ico) == forms


def test_ico_forms_rejects_garbage():
    with pytest.raises(ValueError):
        tools.ico_forms("SK2020")


def test_snippet_marks_matches_and_drops_markup():
    assert parse.snippet('IČO: <span class="highlight-substring">31 322 832</span>, &amp; spol.') == "IČO: «31 322 832», & spol."


def test_match_area_ignores_case_diacritics_and_nbsp():
    assert parse.match_area("obchodne pravo") == ("oblast", "Obchodné právo")
    assert parse.match_area("Starostlivost o maloletych") == ("podoblast", "Starostlivosť o\xa0maloletých")
    assert parse.match_area("konkurz") == ("podoblast", "Konkurz")
    with pytest.raises(ValueError, match="Did you mean"):
        parse.match_area("zmluv")


def test_name_matches():
    assert parse.name_matches("JUDr. Jana Príkladná", "jana prikladna")
    assert parse.name_matches("JUDr. Jana Príkladná", "Prikladna Jana")
    assert parse.name_matches("JUDr. Jana Príkladná, PhD.", "Príkl")
    assert not parse.name_matches("Mgr. Peter Príkladný", "Jana Príkladná")


def test_declarations_parse_and_paging():
    rows, total, has_next = parse.declarations(DECLARATIONS)
    assert total == 3 and not has_next
    assert rows[0] == {
        "name": "JUDr. Jana Príkladná", "court": "Okresný súd Vzorovo", "year": 2024,
        "url": "https://www.sudnarada.gov.sk/data/att/aaa/100.abc.pdf", "size": "57.38 kB",
    }
    _, _, has_next = parse.declarations(declarations_page([], 60, current=1, pages=3))
    assert has_next
    _, _, has_next = parse.declarations(declarations_page([], 60, current=3, pages=3))
    assert not has_next


@pytest.mark.parametrize("url", [
    "https://evil.example/content/public/item/1",
    "https://obcan.justice.sk/pilot/api/ress-isu-service/v1/sud",
    "https://www.sudnarada.gov.sk/9054-sk/",
])
def test_pdf_urls_are_allow_listed(url):
    with pytest.raises(ValueError):
        client_mod.check_pdf_url(url)


@pytest.mark.anyio
async def test_search_by_ico_merges_both_spellings(api):
    result = await tools.court_search_decisions(ico="1234567", limit=5)

    queries = [p.get("query") for path, p in api]
    assert queries == ["01234567", '"1 234 567"']
    assert all(p["sortProperty"] == "datumVydania" and p["sortDirection"] == "DESC" for _, p in api)
    assert result["matching"] == {"by_spelling": {"01234567": 1, '"1 234 567"': 2}, "at_most": 3}
    # Deduplicated and newest first.
    assert [d["guid"] for d in result["decisions"]] == ["b:2", "a:1"]
    assert result["decisions"][1]["snippets"] == ["žalobcu Fiktívna Rafinéria, a.s., IČO: «01234567», proti"]
    assert result["decisions"][1]["date"] == "2025-03-02"
    assert result["by_court_type"] == {"Okresný súd": 4}
    assert "full text" in result["matched_by"]
    assert result["has_more"] is False


@pytest.mark.anyio
async def test_search_by_text_with_filters(api):
    result = await tools.court_search_decisions(
        text="kúpna zmluva", court="okresny sud vzorovo", area="Obchodné právo",
        date_from="2024-01-01", date_to="2024-12-31", limit=2, page=3,
    )

    assert api[0][0] == API + "sud"
    params = api[1][1]
    assert params["guidSud"] == "sud_901"  # the exact name wins over "... - okolie"
    assert params["oblastPravnejUpravyFacetFilter"] == "Obchodné právo"
    assert (params["vydaniaOd"], params["vydaniaDo"]) == ("2024-01-01", "2024-12-31")
    assert (params["query"], params["size"], params["page"]) == ("kúpna zmluva", "2", "3")
    assert result["matching"] == 41 and result["has_more"] is True
    assert "matched_by" not in result


@pytest.mark.anyio
async def test_search_errors_are_reported(api):
    with pytest.raises(ToolError, match="Give ico, text or judge_id"):
        await tools.court_search_decisions()
    with pytest.raises(ToolError, match="could not parse"):
        await tools.court_search_decisions(text="bad (query")
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        await tools.court_search_decisions(text="x", date_from="1.1.2024")
    with pytest.raises(ToolError, match="Several courts"):
        await tools.court_search_decisions(text="x", court="Vzorovo")
    with pytest.raises(ToolError, match="newest 500"):
        await tools.court_search_decisions(ico="1234567", limit=100, page=6)


@pytest.mark.anyio
async def test_get_decision_by_ecli_with_text(api):
    result = await tools.court_get_decision(decision="ECLI:SK:OSVZ:2026:1234567890.3", text=True)

    assert api[0][1]["ecli"] == "ECLI:SK:OSVZ:2026:1234567890.3"
    assert result["guid"] == "c:3"
    assert result["subarea"] == ["Kúpna zmluva"]
    assert result["cited_laws"][0]["law"] == "/SK/ZZ/1991/513"
    assert result["pdf"]["url"] == "https://obcan.justice.sk/content/public/item/3"
    assert "Okresny sud Vzorovo rozhodol" in result["text"]["pages"][0]["text"]

    # A second read of the same PDF comes from memory.
    before = len(api)
    await tools.court_get_decision(decision="c:3", text=True)
    assert len(api) == before + 1


@pytest.mark.anyio
async def test_get_decision_not_found(api):
    with pytest.raises(ToolError, match="does not exist"):
        await tools.court_get_decision(decision="x:9")


@pytest.mark.anyio
async def test_judge_with_declarations(api):
    result = await tools.court_judge(name="Jana Príkladná")

    judge = result["judges"][0]
    assert judge == {
        "judge_id": "sudca_77", "name": "JUDr. Jana PRÍKLADNÁ", "function": "Sudca", "status": "active",
        "court": "Okresný súd Vzorovo", "court_id": "sud_901", "temporarily_assigned_to": "Krajský súd vo Vzorove",
    }
    # Súdna rada is asked for the surname without diacritics; Peter Príkladný is filtered out.
    assert [p.get("nazov") for path, p in api if path.startswith("/9054")] == ["prikladna"]
    assert [(d["name"], d["year"]) for d in result["asset_declarations"]] == [("JUDr. Jana Príkladná", 2025), ("JUDr. Jana Príkladná", 2024)]
    assert result["asset_declarations_matched_by"] == "name"


@pytest.mark.anyio
async def test_judge_surname_first_falls_back(api):
    result = await tools.court_judge(name="Prikladna Jana")

    assert [p.get("nazov") for path, p in api if path.startswith("/9054")] == ["jana", "prikladna"]
    assert len(result["asset_declarations"]) == 2


@pytest.mark.anyio
async def test_read_declaration(api):
    result = await tools.court_judge(declaration_url="https://www.sudnarada.gov.sk/data/att/bbb/200.def.pdf")
    assert "Majetkove priznanie sudcu za rok 2025" in result["pages"][0]["text"]

    with pytest.raises(ToolError, match="did not return a PDF"):
        await tools.court_judge(declaration_url="https://www.sudnarada.gov.sk/data/att/zzz/1.x.pdf")
    with pytest.raises(ToolError, match="can be read"):
        await tools.court_judge(declaration_url="https://example.com/a.pdf")
