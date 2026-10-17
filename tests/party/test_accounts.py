"""Transparent-account tests against a mock of the Slovenská sporiteľňa API. The accounts, people and
companies are fictional; the record shapes are copied from real api.slsp.sk responses."""

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sk_public_money.sources.party import client as client_mod
from sk_public_money.sources.party import parse, tools

PARTY_IBAN = "SK0000000000001111222233"
CAMPAIGN_IBAN = "SK0000000000004444555566"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def account(name, iban, cents):
    return {
        "created": "2026-03-23T09:31:36.268", "updated": "2026-10-17T09:18:59.585", "name": name, "iban": iban,
        "balance": {"value": cents, "precision": 2, "currency": "EUR"}, "published": "2026-03-23T09:31:36.26", "hidden": False,
    }


ACCOUNTS = [
    account("STRANA VZOROVÁ - voľby 2026", PARTY_IBAN, 30988135),
    account("Ján Ukážkový VOĽBY 2026", CAMPAIGN_IBAN, 12822),
    account("Zbierka pre útulok", "SK0000000000007777888899", 500),
]


def turnover(tid, day, cents, counterparty, note, *, vs=None, kind="SEPA platba (elektronicky)"):
    return {
        "turnoverId": str(tid), "accountIban": PARTY_IBAN, "counterAccountName": counterparty, "date": day,
        "amount": {"value": cents, "precision": 2, "currency": "EUR"},
        "balance": {"value": 0, "precision": 2, "currency": "EUR"},
        "note": note, "description": kind, "specific": None, "variable": vs,
    }


TURNOVERS = [
    turnover(9, "2026-10-15", -246, None, "/VS100260039/SS/KS 15.10.2026 615,00 EUR", kind="Transakčná daň"),
    turnover(8, "2026-10-15", -61500, "FIKTÍVNE NOVINY, a.s.", "Zalohova fa c.91-inzercia", vs="100260039"),
    turnover(7, "2026-09-30", 131856, "Strana Vzorová", "prevod", vs="2670443", kind="Prijatá SEPA platba"),
    turnover(6, "2026-09-02", 5000, "Mária Fiktívna", "dar", kind="Prijatá SEPA platba"),
]


def mock_api(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        q = request.url.params
        if request.url.path == "/ta-accounts/api/accounts":
            size, page = int(q["size"]), int(q["page"])
            chunk = ACCOUNTS[page * size : (page + 1) * size]
            pages = -(-len(ACCOUNTS) // size)
            return httpx.Response(200, json={"accounts": chunk, "page": {"size": size, "number": page, "totalElements": len(ACCOUNTS), "totalPages": pages}})
        if request.url.path == f"/ta-turnovers/api/accounts/{PARTY_IBAN}/turnovers":
            rows = TURNOVERS
            if "search" in q:
                rows = [t for t in rows if q["search"].lower() in (t["counterAccountName"] or "").lower()]
            if "amountFrom" in q:
                rows = [t for t in rows if t["amount"]["value"] / 100 >= float(q["amountFrom"])]
            if "amountTo" in q:
                rows = [t for t in rows if t["amount"]["value"] / 100 <= float(q["amountTo"])]
            if "dateFrom" in q:
                rows = [t for t in rows if t["date"] >= q["dateFrom"]]
            size, page = int(q["size"]), int(q["page"])
            return httpx.Response(200, json={"turnovers": rows[page * size : (page + 1) * size], "page": {"size": size, "number": page, "totalElements": len(rows), "totalPages": -(-len(rows) // size)}})
        return httpx.Response(200, json={"turnovers": [], "page": {"size": 1, "number": 0, "totalElements": 0, "totalPages": 0}})

    return handler


@pytest.fixture
def api(monkeypatch, tmp_path):
    calls: list[httpx.Request] = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock_api(calls)))
    monkeypatch.setattr(tools, "_slsp", client_mod.SLSPClient(http=http, cache=tmp_path, interval=0))
    monkeypatch.setattr(client_mod, "ACCOUNTS_PAGE", 2)
    return calls


def test_parse_turnover_drops_unpublished_balance():
    row = parse.turnover(TURNOVERS[1])
    assert row == {
        "date": "2026-10-15", "amount_eur": -615.0, "counterparty": "FIKTÍVNE NOVINY, a.s.",
        "note": "Zalohova fa c.91-inzercia", "type": "SEPA platba (elektronicky)", "vs": "100260039", "id": "8",
    }


def test_match_accounts_folds_and_lists_exact_name_first():
    names = lambda hits: [a["name"] for a in hits]  # noqa: E731
    assert names(tools.match_accounts(ACCOUNTS, "volby 2026")) == ["STRANA VZOROVÁ - voľby 2026", "Ján Ukážkový VOĽBY 2026"]
    assert names(tools.match_accounts(ACCOUNTS, "vzorova")) == ["STRANA VZOROVÁ - voľby 2026"]
    two = ACCOUNTS + [account("Zbierka pre útulok Nitra", "SK0000000000009999000011", 1)]
    assert names(tools.match_accounts(two, "zbierka pre utulok")) == ["Zbierka pre útulok", "Zbierka pre útulok Nitra"]


@pytest.mark.anyio
async def test_transactions_by_account_name(api):
    out = await tools.party_account_transactions(account="strana vzorova")
    assert out["account"] == {"name": "STRANA VZOROVÁ - voľby 2026", "iban": PARTY_IBAN, "balance_eur": 309881.35, "opened": "2026-03-23", "updated": "2026-10-17T09:18:59"}
    assert out["matching"] == 4 and len(out["transactions"]) == 4 and not out["has_more"]
    assert "matched_by" not in out
    # The account list was paged and cached: a second call fetches only turnovers.
    pages = [c for c in api if c.url.path.endswith("/accounts")]
    assert len(pages) == 2
    await tools.party_account_transactions(iban=PARTY_IBAN)
    assert len([c for c in api if c.url.path.endswith("/accounts")]) == 2


@pytest.mark.anyio
async def test_several_matching_accounts_are_listed(api):
    out = await tools.party_account_transactions(account="volby")
    assert out["matching_accounts"] == 2 and "transactions" not in out
    assert {a["iban"] for a in out["accounts"]} == {PARTY_IBAN, CAMPAIGN_IBAN}


@pytest.mark.anyio
async def test_filters_go_to_the_api(api):
    out = await tools.party_account_transactions(iban="SK00 0000 0000 0011 1122 2233", counterparty="noviny", date_from="2026-10-01")
    assert [t["counterparty"] for t in out["transactions"]] == ["FIKTÍVNE NOVINY, a.s."]
    assert out["matched_by"] == "name"
    q = api[-1].url.params
    assert q["search"] == "noviny" and q["dateFrom"] == "2026-10-01"


@pytest.mark.anyio
async def test_min_amount_both_ways_merges_two_queries(api):
    out = await tools.party_account_transactions(iban=PARTY_IBAN, min_amount=100)
    assert [t["amount_eur"] for t in out["transactions"]] == [-615.0, 1318.56]
    assert out["matching"] == 2
    out = await tools.party_account_transactions(iban=PARTY_IBAN, direction="in")
    assert [t["amount_eur"] for t in out["transactions"]] == [1318.56, 50.0]
    assert api[-1].url.params["amountFrom"] == "0.01"


@pytest.mark.anyio
async def test_unknown_or_bad_iban(api):
    with pytest.raises(ToolError, match="not a published"):
        await tools.party_account_transactions(iban="SK1100000000009999999999")
    with pytest.raises(ToolError, match="22 digits"):
        await tools.party_account_transactions(iban="CZ123")
    with pytest.raises(ToolError, match="exactly one"):
        await tools.party_account_transactions()
