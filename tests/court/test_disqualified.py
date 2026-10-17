"""The register of disqualified persons: local copy, refresh, name matching and flags for RPO.
All people are fictional; record shapes are copied from real obcan.justice.sk responses."""

import io
import time
from datetime import date

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sk_public_money import localdb
from sk_public_money.sources.court import client as client_mod
from sk_public_money.sources.court import disqualified, tools


@pytest.fixture
def anyio_backend():
    return "asyncio"


LIST = [
    {"registreGuid": "diskvalifikacia_10", "meno": "Peter Široký", "adresa": "Hlavná 12/3, 94901 Nitra", "datumRozhodnutia": "03.02.2022", "sud": "Okresný súd Vzorovo"},
    {"registreGuid": "diskvalifikacia_11", "meno": "Mária Siroká", "adresa": "Felhő utca 2, 4722 Kisfalu", "datumRozhodnutia": "11.06.2026"},
    {"registreGuid": "diskvalifikacia_12", "meno": "Győző Kovács", "adresa": "Dlhá 1, 92901 Dunajská Streda", "datumRozhodnutia": "01.01.2025"},
]
DETAILS = {
    "diskvalifikacia_10": {"spisovaZnacka": "1T 54/2021", "cisloKonania": "1T 54/2021-553", "vylucenieOd": "03.02.2022", "vylucenieDo": "03.08.2024", "updateDate": "20.04.2022"},
    "diskvalifikacia_11": {"spisovaZnacka": "103642821/2026", "cisloKonania": "103642821/2026", "poznamka": "CbVyl 244/26, DÚ Vzorovo",
                           "vylucenieOd": "15.07.2026", "vylucenieDo": "15.07.2099", "updateDate": "14.10.2026"},
    "diskvalifikacia_12": {"spisovaZnacka": "2T 1/2025", "updateDate": "02.05.2025"},
}


@pytest.fixture
def register(monkeypatch, tmp_path):
    state = {"list": list(LIST), "down": False, "seen": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        state["seen"].append(path)
        if state["down"]:
            raise httpx.ConnectError("connection refused")
        if path == "diskvalifikacia":
            return httpx.Response(200, json={"numFound": len(state["list"]), "diskvalifikaciaList": state["list"]})
        if path in DETAILS:
            base = next(r for r in LIST if r["registreGuid"] == path)
            return httpx.Response(200, json={**base, **DETAILS[path]})
        return httpx.Response(404, json={"message": f"diskvalifikacia does not exist. Id: {path}"})

    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    monkeypatch.setattr(tools, "_client", client_mod.CourtClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    return state


def test_name_tokens_drop_titles():
    assert disqualified.name_tokens("Ing. Ján Vzorový, PhD.") == {"jan", "vzorovy"}
    assert disqualified.name_tokens("JUDr. Ing. Eva Nová-Stará MBA") == {"eva", "nova-stara"}


@pytest.mark.parametrize("a, b, expected", [
    ("Peter Široký", "Ing. PETER SIROKY", "exact"),
    ("Siroky Peter", "Peter Široký", "exact"),
    ("Peter Široký", "Peter Pavol Široký", "partial"),
    ("Peter Široký", "Pavol Široký", None),
    ("Široký", "Peter Široký", None),
    ("Győző Kovács", "Gyozo Kovacs", "exact"),
])
def test_name_match(a, b, expected):
    assert disqualified.name_match(a, b) == expected


@pytest.mark.parametrize("a, b, expected", [
    ("Hlavná 12/3, 94901 Nitra", "Hlavná 12/3, 949 01 Nitra", "same address"),
    ("Hlavná 12/3, 94901 Nitra", "Dlhá 7, 94901 Nitra", "same town"),
    ("Hlavná 12/3, 94901 Nitra", "Hlavná 12/3, 81101 Bratislava", "different"),
    ("Hlavná 12/3, 94901 Nitra", None, "not comparable"),
])
def test_address_match(a, b, expected):
    assert disqualified.address_match(a, b) == expected


def test_ban_active():
    today = date(2026, 10, 17)
    assert disqualified.ban_active("2022-02-03", "2024-08-03", today) is False
    assert disqualified.ban_active("2026-07-15", "2099-07-15", today) is True
    assert disqualified.ban_active("2026-11-01", "2029-11-01", today) is False  # not yet in force
    assert disqualified.ban_active(None, None, today) is None


@pytest.mark.anyio
async def test_search_ignores_diacritics_and_shows_ban(register):
    result = await tools.court_disqualified(name="sirok")  # word start: Široký and Siroká

    assert result["matching"] == 2 and result["matched_by"] == "name"
    assert [r["record_id"] for r in result["records"]] == ["diskvalifikacia_11", "diskvalifikacia_10"]  # newest first
    newest, older = result["records"]
    assert newest == {
        "record_id": "diskvalifikacia_11", "name": "Mária Siroká", "address": "Felhő utca 2, 4722 Kisfalu",
        "decision_date": "2026-06-11", "court": None, "case_number": "103642821/2026", "note": "CbVyl 244/26, DÚ Vzorovo",
        "ban_from": "2026-07-15", "ban_to": "2099-07-15", "ban_active": True, "updated": "2026-10-14",
    }
    assert older["ban_active"] is False and older["proceedings_number"] == "1T 54/2021-553"

    active = await tools.court_disqualified(name="Širok", active_only=True)
    assert [r["record_id"] for r in active["records"]] == ["diskvalifikacia_11"]


@pytest.mark.anyio
async def test_record_without_period_and_by_id(register):
    rec = (await tools.court_disqualified(record_id="diskvalifikacia_12"))["records"][0]
    assert rec["ban_active"] is None and rec["ban_period"] == "not given in the record"

    with pytest.raises(ToolError, match="No record"):
        await tools.court_disqualified(record_id="diskvalifikacia_99")
    with pytest.raises(ToolError, match="Give a name"):
        await tools.court_disqualified()


@pytest.mark.anyio
async def test_list_is_read_once_a_day_and_mirrors_removals(register):
    await tools.court_disqualified(name="Kovacs")
    await tools.court_disqualified(name="Kovacs")
    assert register["seen"].count("diskvalifikacia") == 1
    assert register["seen"].count("diskvalifikacia_12") == 1  # details are kept

    # A day later the register no longer lists Győző Kovács.
    register["list"] = LIST[:2]
    db = disqualified.open_db()
    localdb.set_meta(db, "list", str(time.time() - 25 * 3600))
    db.close()
    assert (await tools.court_disqualified(name="Kovacs"))["matching"] == 0


@pytest.mark.anyio
async def test_unreachable_register(register):
    register["down"] = True
    with pytest.raises(ToolError, match="could not be downloaded"):
        await tools.court_disqualified(name="Kovacs")
    people = [{"name": "Peter Široký", "address": None}]
    assert "not done" in await disqualified.flag(people)
    assert "disqualified" not in people[0]

    # With an old copy the check still runs and says how old it is.
    register["down"] = False
    await tools.court_disqualified(name="Kovacs")
    db = disqualified.open_db()
    localdb.set_meta(db, "list", str(time.time() - 25 * 3600))
    db.close()
    register["down"] = True
    note = await disqualified.flag(people)
    assert "using the copy from" in note
    assert people[0]["disqualified"][0]["record_id"] == "diskvalifikacia_10"


@pytest.mark.anyio
async def test_flag_people_not_companies(register):
    parties = [
        {"role": "Konateľ", "name": "Ing. Peter Široký", "address": "Iná 5, 94901 Nitra"},
        {"role": "Prokurista", "name": "Mária Siroká", "address": "Hlavná 1, 81101 Bratislava"},
        {"role": "Spoločník", "name": "Peter Široký s.r.o.", "ico": "01234567", "address": "Hlavná 12/3, 94901 Nitra"},
        {"role": "Konateľ", "name": "Pavol Široký", "address": None},
    ]
    assert await disqualified.flag(parties) is None

    first = parties[0]["disqualified"]
    assert first == [{
        "record_id": "diskvalifikacia_10", "matched_by": "name and town", "name_match": "exact",
        "register_name": "Peter Široký", "register_address": "Hlavná 12/3, 94901 Nitra", "address_comparison": "same town",
        "ban_from": "2022-02-03", "ban_to": "2024-08-03", "ban_active": False, "decision_date": "2022-02-03",
        "court": "Okresný súd Vzorovo",
    }]
    assert parties[1]["disqualified"][0]["matched_by"] == "name"
    assert parties[1]["disqualified"][0]["address_comparison"] == "different"
    assert "disqualified" not in parties[2] and "disqualified" not in parties[3]


def test_officers_are_directors_and_procurists():
    org = {
        "statutory_bodies": [{"role": "Konateľ", "name": "A B"}],
        "stakeholders": [{"role": "Prokurista", "name": "C D"}, {"role": "Spoločník", "name": "E F"}],
    }
    assert [p["name"] for p in disqualified.officers(org)] == ["A B", "C D"]


@pytest.mark.anyio
async def test_sync_fetches_every_detail(register):
    from sk_public_money.sources.court import sync

    stats = await sync.sync(client=tools.client(), out=io.StringIO())
    assert stats == {"records": 3, "details": 3}
    db = disqualified.open_db()
    assert db.execute("SELECT count(*) FROM disqualified WHERE detail IS NOT NULL").fetchone()[0] == 3
    db.close()
