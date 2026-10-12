"""Vestník tests: parsing an issue, the SQLite index, syncing and the combined search. The issue in
fixtures/vestnik_issue.json is fictional, built in the shape of real data.slovensko.sk downloads
(form trees whose panels carry their IDs only in their labels)."""

import gzip
import json
import time
from pathlib import Path

import httpx
import pytest

from sk_public_money.sources.uvo import client as client_mod
from sk_public_money.sources.uvo import index as index_mod
from sk_public_money.sources.uvo import sync, tools, vestnik

FIXTURES = Path(__file__).parent / "fixtures"
ISSUE = (FIXTURES / "vestnik_issue.json").read_bytes()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def parsed():
    return vestnik.parse_issue(ISSUE)


def test_parse_issue(parsed):
    meta, notices = parsed
    assert meta == {"year": 2026, "number": 207, "published": "2026-10-12"}
    # The result notice, two procurements of the summary report; ÚVO's XML supervision notice is skipped.
    assert [n["id"] for n in notices] == ["vestnik:900101", "vestnik:900202-1", "vestnik:900202-2"]


def test_broken_item_is_skipped_not_fatal():
    issue = json.loads(ISSUE)
    issue["bulletinItemList"].insert(0, {"itemData": '{"name": "Oznámenie", "components": "oops"}', "submissionId": 1})
    meta, notices = vestnik.parse_issue(json.dumps(issue))
    assert len(notices) == 3
    assert meta["skipped"][0].startswith("item 1: KeyError")


def test_eforms_notice(parsed):
    notice = parsed[1][0]
    assert notice["notice_uuid"] == "bbbbbbbb-0000-4000-8000-000000000001"
    assert notice["uvo_procurement_id"] == 700001
    assert notice["buyers"][0]["ico"] == "00312345"
    assert notice["buyers"][0]["contact"]["name"] == "Mgr. Ján Vzorový"
    assert notice["estimated_value"] == {"amount": 250000, "currency": "EUR"}

    lot1, lot2 = notice["lots"]
    result = lot1["results"][0]
    assert result["bids"] == {"tenders": 2, "sme_tenders": 1}
    winner, loser = result["tenders"]
    # Tendering parties are matched by the IDs in their labels, not by position.
    assert (winner["id"], winner["winner"], winner["tenderers"][0]["name"]) == ("TEN-0002", True, "Ukážka Palivá s.r.o.")
    assert winner["tenderers"][0]["ico"] == "07654321"
    assert (loser["id"], loser["winner"], loser["tenderers"][0]["ico"]) == ("TEN-0001", False, "01234567")
    # No contract reference in the result: the contract is found through its tender.
    assert result["contracts"][0]["crz_id"] == 99000002
    assert lot2["results"][0] == {"status": "closed without a winner", "no_winner_reason": "no-rece", "bids": {"tenders": 0}}
    assert notice["notes"]  # framework agreement


def test_summary_report(parsed):
    first, second = parsed[1][1:]
    assert first["report_type"] == "Súhrnná správa o zákazkách s nízkymi hodnotami"
    assert first["period"] == "2026 H1"
    assert first["contract_nature"] == "Tovary"
    assert first["buyers"][0]["ico"] == "00999998"
    result = first["lots"][0]["results"][0]
    assert result["bids"] == {"tenders": 3, "electronic_tenders": 3}
    contract = result["contracts"][0]
    assert contract["value_excl_vat"] == {"amount": 4214.07, "currency": "EUR"}
    assert contract["crz_id"] == 99000003
    assert contract["suppliers"][0]["contact"] == {"name": "Peter Príkladný", "email": "info@ukazka-paliva.sk"}
    foreign = second["lots"][0]["results"][0]["contracts"][0]["suppliers"][0]
    assert foreign["ico"] is None and foreign["country"] == "AUT"
    assert "framework_agreement" not in second


# --- index -------------------------------------------------------------------------------------

@pytest.fixture
def ix(tmp_path, parsed):
    index = index_mod.VestnikIndex(tmp_path / "vestnik.sqlite")
    index.add_issue(*parsed, dataset="https://data.gov.sk/set/x", modified="2026-10-12T10:21:50Z")
    yield index
    index.close()


def test_index_search(ix):
    rows, total = ix.search(icos=["07654321"], role="winner")
    assert total == 2  # won the tender and supplied under the low-value report
    assert {r["id"] for r in rows} == {"vestnik:900101", "vestnik:900202-1"}  # same day: order by ID
    assert ix.search(icos=["01234567"], role="winner")[1] == 0
    assert ix.search(icos=["01234567"], role="tenderer")[1] == 1
    assert ix.search(icos=["01234567"], role="any")[1] == 0  # any = buyer or winner
    assert ix.search(icos=["07654321"], role="tenderer")[1] == 2  # winners, report suppliers included, bid too
    assert ix.search(icos=["00312345"], role="any")[1] == 1
    assert ix.search(icos=["00312345"], role="buyer")[1] == 1
    assert ix.roles_of(["vestnik:900101"], "01234567") == {"vestnik:900101": ["tenderer"]}
    # Words ignore diacritics and Slovak endings.
    assert ix.search(text="motorova nafta")[1] == 1
    assert ix.search(text="CISTIACE")[1] == 1
    assert ix.search(cpv_prefix="091342")[1] == 1
    assert ix.search(published_from="2026-10-13")[1] == 0


def test_index_row_and_detail(ix):
    row = ix.search(icos=["00312345"], role="buyer")[0][0]
    assert row["winners"] == [{"name": "Ukážka Palivá s.r.o.", "ico": "07654321"}]
    assert row["crz_ids"] == [99000002]
    assert row["tenders_received"] == [2, 0]
    report_row = ix.search(text="traktor")[0][0]
    assert report_row["value_excl_vat"] == {"amount": 4214.07, "currency": "EUR"}
    assert ix.get("vestnik:900101")["lots"][0]["id"] == "LOT-0001"
    assert ix.has_uuids(["bbbbbbbb-0000-4000-8000-000000000001", "x"]) == {"bbbbbbbb-0000-4000-8000-000000000001"}


def test_index_replaces_issue(ix, parsed):
    meta, notices = parsed
    ix.add_issue(meta, notices[:1], modified="2026-10-13T08:00:00Z")
    assert ix.search(awards_only=False)[1] == 1
    assert ix.search(icos=["00999998"])[1] == 0
    assert ix.issue_state() == {(2026, 207): "2026-10-13T08:00:00Z"}


# --- sync --------------------------------------------------------------------------------------

def sparql(*issues):
    """A SPARQL JSON answer listing (number, year, modified, url) issues."""
    rows = [{
        "dataset": {"type": "uri", "value": f"https://data.gov.sk/set/{n}-{y}"},
        "title": {"type": "literal", "value": f"Vestník {n}/{y}"},
        "issued": {"type": "literal", "value": m},
        "modified": {"type": "literal", "value": m},
        "url": {"type": "uri", "value": url},
    } for n, y, m, url in issues]
    return {"head": {"vars": []}, "results": {"bindings": rows}}


@pytest.fixture
def portal(monkeypatch, tmp_path):
    seen = []
    listing = sparql(
        (206, 2026, "2026-10-12T10:13:13Z", "https://data.slovensko.sk/download?id=206"),
        (207, 2026, "2026-10-12T10:21:50Z", "https://data.slovensko.sk/download?id=207"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        if request.url.path == "/api/sparql":
            return httpx.Response(200, json=listing)
        if request.url.params.get("id") == "207":
            return httpx.Response(200, content=ISSUE)
        return httpx.Response(404)

    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    return sync.CatalogueClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), interval=0), seen


@pytest.mark.anyio
async def test_list_and_ingest(portal, tmp_path):
    catalogue, seen = portal
    listed = await catalogue.list_issues()
    assert [(i["year"], i["number"]) for i in listed] == [(2026, 206), (2026, 207)]
    index = index_mod.VestnikIndex(tmp_path / "v.sqlite")
    assert await sync.ingest(catalogue, index, listed[1], keep_raw=True) == 3
    assert gzip.decompress(sync.raw_path(2026, 207).read_bytes()) == ISSUE
    # Indexing again (after a program update) reads the kept file instead of downloading.
    await sync.ingest(catalogue, index, listed[1], keep_raw=True)
    assert sum(1 for u in seen if u.params.get("id") == "207") == 1


@pytest.mark.anyio
async def test_refresh_fetches_only_newer_issues(portal, tmp_path, parsed):
    catalogue, seen = portal
    index = index_mod.VestnikIndex(tmp_path / "v.sqlite")
    meta, notices = parsed
    index.add_issue({**meta, "number": 206}, [], modified="2026-10-12T10:13:13Z")
    assert await sync.refresh(index, catalogue) is None
    assert (2026, 207) in index.issue_state()
    # Checked a moment ago: no request at all.
    seen.clear()
    assert await sync.refresh(index, catalogue) is None
    assert seen == []


def test_pending():
    listed = [{"year": 2026, "number": n, "modified": m} for n, m in ((1, "2026-01-02"), (2, "2026-01-05"), (3, "2026-01-06"))]
    state = {(2026, 1): "2026-01-02", (2026, 2): "2026-01-03"}
    assert [i["number"] for i in sync.pending(listed, state)] == [2, 3]


# --- combined search ---------------------------------------------------------------------------

TED_ROW = {
    "publication-number": "600001-2023", "publication-date": "2023-09-01+02:00", "notice-type": "can-standard",
    "title-proc": {"slk": "Staršia zákazka"}, "buyer-name": {"slk": ["Fiktívne mesto Horná Ves"]}, "buyer-identifier": ["00312345"],
}
DUPLICATE = {**TED_ROW, "publication-number": "600002-2026", "notice-identifier": "bbbbbbbb-0000-4000-8000-000000000001"}


@pytest.fixture
def combined(monkeypatch, ix):
    queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(json.loads(request.content)["query"])
        return httpx.Response(200, json={"notices": [DUPLICATE, TED_ROW], "totalNoticeCount": 2})

    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    monkeypatch.setattr(tools, "_client", client_mod.TEDClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(tools, "_index", ix)
    ix.set_meta("checked_at", str(time.time()))
    return queries


@pytest.mark.anyio
async def test_combined_search(combined):
    result = await tools.uvo_search_procurements(ico="312345", role="buyer")
    # TED is asked only for the time before the index begins.
    assert "publication-date<=20261011" in combined[0]
    assert [r["id"] for r in result["results"]] == ["vestnik:900101", "ted:600001-2023"]
    assert result["results"][0]["matched_as"] == ["buyer"]
    assert result["vestnik_index"] == {"issues": 1, "from": "2026-10-12", "to": "2026-10-12"}


@pytest.mark.anyio
async def test_tenderer_search_says_what_ted_covers(combined):
    result = await tools.uvo_search_procurements(ico="01234567", role="tenderer")
    assert result["results"][0]["matched_as"] == ["tenderer"]
    assert tools.TED_ROLE_NOTES["tenderer"] in result["ted_note"]


@pytest.mark.anyio
async def test_source_vestnik_and_get(combined):
    result = await tools.uvo_search_procurements(text="nafta", source="vestnik", awards_only=False)
    assert combined == [] and result["total"] == 2
    notice = await tools.uvo_get_notice("vestnik:900202-1")
    assert notice["title"] == "Dodávka nafty pre obecný traktor"
