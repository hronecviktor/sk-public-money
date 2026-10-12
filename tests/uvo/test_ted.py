"""Procurement tests against TED: notice XML parsing and a mocked Search API. All organisations,
people and amounts are fictional; the shapes are copied from real TED responses."""

import json
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sk_public_money.sources.uvo import client as client_mod
from sk_public_money.sources.uvo import parse, tools

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- identifiers -------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "country", "expected"),
    [
        ("31322832", None, {"id": "31322832", "kind": "ico"}),
        ("31 322 832", None, {"id": "31322832", "kind": "ico"}),
        ("151866", None, {"id": "00151866", "kind": "ico", "note": "given as 151866, zero-padded"}),
        ("2020372640", None, {"id": "2020372640", "kind": "dic"}),
        ("SK2020372640", None, {"id": "SK2020372640", "kind": "ic_dph"}),
        ("CZ27176100", None, {"id": "CZ27176100", "kind": "foreign", "country": "CZ"}),
        ("27176100", "CZE", {"id": "27176100", "kind": "foreign", "country": "CZE"}),
        ("123", None, {"id": "123", "kind": "unknown"}),
    ],
)
def test_classify_id(raw, country, expected):
    assert parse.classify_id(raw, country) == expected


def test_party_without_slovak_ico_says_so():
    p = parse.party("Vzorek Papír s.r.o.", ["12345678", "CZ12345678"], "CZE")
    assert p["ico"] is None
    assert "No Slovak IČO" in p["ico_note"]
    assert list(p)[:2] == ["name", "ico"]


def test_crz_id():
    assert parse.crz_id("https://crz.gov.sk/zmluva/12874883/") == 12874883
    assert parse.crz_id("https://www.crz.gov.sk/index.php?ID=8818627") == 8818627
    assert parse.crz_id("https://www.example.sk/zmluvy/1") is None


# --- notice XML --------------------------------------------------------------------------------

def test_eforms_award_notice():
    notice = parse.parse_notice((FIXTURES / "ted_eforms_can.xml").read_bytes(), "900001-2026")
    assert notice["id"] == "ted:900001-2026"
    assert notice["published"] == "2026-10-05"
    assert notice["notice_uuid"] == "11111111-2222-4333-8444-555555555555"
    assert notice["procedure_type"] == "open"
    assert notice["cpv"] == ["30197630", "30197643"]

    buyer = notice["buyers"][0]
    assert buyer["ico"] == "00312345"  # typed without leading zeros
    assert buyer["other_ids"] == [{"id": "2020999999", "kind": "dic"}]
    assert buyer["contact"] == {"name": "Mgr. Ján Vzorový", "email": "obstaravanie@fiktivne-mesto.sk", "phone": "+421 900 000 001"}

    lot1, lot2 = notice["lots"]
    result = lot1["results"][0]
    assert result["status"] == "winner chosen"
    assert result["bids"] == {"tenders": 2, "sme_tenders": 1}
    assert result["lowest_bid"] == {"amount": 790500.5, "currency": "EUR"}

    winner, loser = result["tenders"]
    assert winner["winner"] and not loser["winner"]
    assert winner["amount"]["amount"] == 790500.5
    # A consortium of a Slovak and a Czech firm, with a subcontractor.
    assert [t["name"] for t in winner["tenderers"]] == ["Ukážka Kancel s.r.o.", "Vzorek Papír s.r.o."]
    assert winner["tenderers"][0]["ico"] == "07654321"
    assert winner["tenderers"][1]["ico"] is None
    assert winner["subcontractors"][0]["ico"] == "87654321"
    assert loser["tenderers"][0]["other_ids"] == [{"id": "SK2020111111", "kind": "ic_dph"}]

    contract = result["contracts"][0]
    assert contract == {
        "number": "RD 12/2026", "title": "Rámcová dohoda - Nákup kancelárskeho papiera", "awarded": "2026-08-12",
        "concluded": "2026-09-29", "url": "https://crz.gov.sk/zmluva/99000001/", "crz_id": 99000001, "tenders": ["TEN-0002"],
    }

    assert lot2["results"][0]["status"] == "closed without a winner"
    assert lot2["results"][0]["no_winner_reason"] == "no-rece"
    assert lot2["results"][0]["bids"] == {"tenders": 0}

    assert notice["framework_agreement"] is True
    assert notice["framework_maximum"]["amount"] == 800000
    assert parse.FRAMEWORK_NOTE in notice["notes"]


def test_legacy_award_notice():
    notice = parse.parse_notice((FIXTURES / "ted_legacy_f03.xml").read_bytes(), "900-2022")
    assert notice["published"] == "2022-01-03"
    assert notice["form_type"] == "F03"
    assert notice["title"] == "Lieky skupiny X"  # the Slovak original, not the translation
    assert notice["procedure_type"] == "open"
    assert notice["buyers"][0]["ico"] == "00999999"
    assert notice["buyers"][0]["contact"]["name"] == "Ing. Eva Ukážková"
    lot1, lot2 = notice["lots"]
    tender = lot1["results"][0]["tenders"][0]
    assert tender["tenderers"][0]["ico"] == "01234567"
    assert tender["amount"]["amount"] == 400000
    assert lot1["estimated_value"]["amount"] == 415000
    assert lot1["results"][0]["bids"] == {"tenders": 4, "electronic_tenders": 4}
    assert lot1["results"][0]["contracts"][0]["number"] == "663/2021"
    assert lot2["results"][0]["no_winner_reason"] == "procurement_unsuccessful"
    assert "format_note" in notice


# --- search ------------------------------------------------------------------------------------

def ted_row(number, **fields):
    """A search hit as TED returns it: every field a flat list or a per-language dict."""
    return {"publication-number": number, "links": {"xml": {"MUL": f"https://ted.europa.eu/en/notice/{number}/xml"}}, **fields}


ROWS = [
    ted_row(
        "900001-2026",
        **{
            "publication-date": "2026-10-05+02:00", "notice-type": "can-standard", "procedure-type": "open",
            "title-proc": {"slk": "Nákup kancelárskeho papiera"},
            "buyer-name": {"slk": ["Fiktívne mesto Horná Ves"]}, "buyer-identifier": ["00312345", "2020999999"],
            "winner-name": {"slk": ["Papier Príklad, a.s.", "Vzorek Papír s.r.o."]},
            "winner-identifier": ["01234567", "2020111111", "12345678", "CZ12345678"], "winner-country": ["SVK", "CZE"],
            "classification-cpv": ["30197630", "30197630"], "total-value": 812000, "total-value-cur": ["EUR"],
            "BT-118-NoticeResult": "800000.00", "BT-759-LotResult": ["2", "0", "1"],
            "received-submissions-type-code": ["tenders", "part-req", "t-sme"], "contract-identifier": ["RD 12/2026"],
            "notice-identifier": "11111111-2222-4333-8444-555555555555",
        },
    ),
    ted_row(
        "900-2022",
        **{"publication-date": "2022-01-03+01:00", "notice-type": "can-standard", "notice-title": {"slk": "Slovensko-Dolná Ves: Lieky"},
           "buyer-name": {"slk": ["Fiktívna nemocnica Dolná Ves"]}, "buyer-identifier": ["00999999"], "total-value": 0},
    ),
]


def test_search_row_pairs_names_and_ids():
    row = parse.search_row(ROWS[0])
    assert row["id"] == "ted:900001-2026"
    assert row["buyers"] == [{"name": "Fiktívne mesto Horná Ves", "ico": "00312345", "other_ids": [{"id": "2020999999", "kind": "dic"}]}]
    assert [w["ico"] for w in row["winners"]] == ["01234567", None]
    assert row["winners"][1]["country"] == "CZE"
    assert row["cpv"] == ["30197630"]
    assert row["tenders_received"] == [2]
    assert row["framework_maximum"] == {"amount": 800000, "currency": "EUR"}
    assert parse.search_row(ROWS[1])["notes"] == [parse.ZERO_NOTE]


def test_search_row_leaves_ambiguous_ids_unpaired():
    row = parse.search_row(ted_row("1-2026", **{"winner-name": {"slk": ["A s.r.o.", "B s.r.o."]}, "winner-identifier": ["01234567"]}))
    assert row["winners"][:2] == [{"name": "A s.r.o."}, {"name": "B s.r.o."}]
    assert row["winners"][2]["unpaired_ids"] == [{"id": "01234567", "kind": "ico"}]


def test_ted_query():
    q = tools.ted_query(ico="312345", role="buyer", text='kancelársky "papier"', cpv="30197630-9",
                        published_from="2026-01-01", published_to="2026-06-30", awards_only=True)
    assert q == (
        "buyer-country=SVK AND buyer-identifier IN (00312345 312345) AND FT~(kancelársky papier) "
        "AND classification-cpv=3019763* AND publication-date>=20260101 AND publication-date<=20260630 "
        "AND form-type=result SORT BY publication-date DESC"
    )
    any_role = tools.ted_query(ico="01234567", role="any", text=None, cpv="09000000", published_from=None,
                               published_to=None, awards_only=False)
    winner = "(winner-identifier IN (01234567 1234567) OR ((FT~(01234567) OR FT~(1234567)) AND publication-date<20231101))"
    assert f"(buyer-identifier IN (01234567 1234567) OR {winner})" in any_role
    assert "tenderer" not in any_role and "subcontractor" not in any_role  # any = buyer or winner only
    role = lambda r: tools.ted_query(ico="01234567", role=r, text=None, cpv=None, published_from=None, published_to=None, awards_only=False)  # noqa: E731
    assert f"(organisation-identifier-tenderer IN (01234567 1234567) OR {winner})" in role("tenderer")
    assert "organisation-identifier-subcontractor IN (01234567 1234567)" in role("subcontractor")
    assert "classification-cpv=09*" in any_role
    with pytest.raises(ValueError):
        tools.ted_query(ico="123456789", role="any", text=None, cpv=None, published_from=None, published_to=None, awards_only=True)


@pytest.fixture
def ted(monkeypatch, tmp_path):
    """Route TED requests to canned responses; records each request."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.ted.europa.eu":
            body = json.loads(request.content)
            return httpx.Response(200, json={"notices": ROWS, "totalNoticeCount": 41, "iterationNextToken": None, "timedOut": False}) \
                if "FT~(((" not in body["query"] else httpx.Response(400, json={"message": "Syntax error"})
        if request.url.path == "/en/notice/900001-2026/xml":
            return httpx.Response(200, content=(FIXTURES / "ted_eforms_can.xml").read_bytes())
        return httpx.Response(404, text="<html>Not found</html>")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # No Vestník index: these tests search TED only.
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tools, "_index", None)
    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    monkeypatch.setattr(tools, "_client", client_mod.TEDClient(http=http, cache=tmp_path))
    return seen


@pytest.mark.anyio
async def test_search_tool(ted):
    result = await tools.uvo_search_procurements(ico="1234567", role="winner", page=1)
    body = json.loads(ted[0].content)
    assert body["page"] == 2 and body["limit"] == 20
    assert "winner-identifier IN (01234567 1234567)" in body["query"]
    assert result["total"] == 41 and result["has_more"] is True
    assert result["note"] == tools.NO_INDEX_NOTE
    # The second hit names the IČO nowhere in its indexed parties: it came from full text.
    assert "matched_by" not in result["results"][0]
    assert result["results"][1]["matched_by"] == tools.TEXT_MATCH_NOTE


@pytest.mark.anyio
async def test_get_notice_tool_caches(ted):
    first = await tools.uvo_get_notice("ted:900001-2026")
    again = await tools.uvo_get_notice("https://ted.europa.eu/sk/notice/-/detail/00900001-2026")
    assert first == again
    assert len(ted) == 1
    with pytest.raises(ToolError, match="No notice 5-2026"):
        await tools.uvo_get_notice("5-2026")
    with pytest.raises(ToolError, match="Not a notice ID"):
        await tools.uvo_get_notice("hello")
