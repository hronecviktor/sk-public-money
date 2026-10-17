"""RPO tests against a mock of the REST API. All people and companies here are fictional;
the record shapes are copied from real api.statistics.sk/rpo/v1 responses."""

import httpx
import pytest

from sk_public_money.http import SourceUnavailable
from sk_public_money.sources.rpo import client as client_mod
from sk_public_money.sources.court import client as court_client
from sk_public_money.sources.court import tools as court_tools
from sk_public_money.sources.rpo import parse, tools

SK = {"value": "Slovenská republika", "code": "703", "codelistCode": "CL000086"}
EUR = {"value": "euro", "code": "EUR", "codelistCode": "CL000083"}


def cv(value, code="1", codelist="CL000000"):
    return {"value": value, "code": code, "codelistCode": codelist}


def addr(street, number, psc, town, **extra):
    return {"street": street, "regNumber": 0, "buildingNumber": number, "postalCodes": [psc],
            "municipality": {"value": town}, "country": SK, **extra}


def person(given, family, **extra):
    return {"formatedName": f"{given} {family}", "familyNames": [family], "givenNames": [given], **extra}


IDENTITY = {
    "id": 700001,
    "dbModificationDate": "2026-07-09",
    "identifiers": [{"value": "01234567", "validFrom": "1995-03-01"}],
    "fullNames": [
        {"value": "Fiktívna Rafinéria a.s.", "validFrom": "1995-03-01", "validTo": "2008-05-29"},
        {"value": "FIKTÍVNA RAFINÉRIA, a.s.", "validFrom": "2008-05-30"},
    ],
    "addresses": [
        {"validFrom": "2006-06-16", **addr("Vymyslená", "1", "82412", "Bratislava")},
        {"validFrom": "1995-03-01", "validTo": "2006-06-15", **addr("Stará", "7", "82412", "Bratislava")},
    ],
    "establishment": "1995-03-01",
    "sourceRegister": {
        "value": cv("Obchodný register", "1", "CL010112"),
        "registrationOffices": [{"value": "Mestský súd Bratislava III", "validFrom": "1995-03-01"}],
        "registrationNumbers": [{"value": "Sa/9999/B", "validFrom": "1995-03-01"}],
    },
}
ENTITY = {
    **IDENTITY,
    "legalForms": [{"value": cv("Akciová spoločnosť", "121", "CL000056"), "validFrom": "1995-03-01"}],
    "activities": [
        {"economicActivityDescription": "výroba fiktívnych palív", "validFrom": "1995-03-01"},
        {"economicActivityDescription": "zrušená činnosť", "validFrom": "1995-03-01", "validTo": "2000-01-01"},
    ],
    "statutoryBodies": [
        {"stakeholderType": cv("Predstavenstvo", "99", "CL010113"), "statutoryBodyMember": {"value": "predseda predstavenstva"},
         "validFrom": "2015-01-01", "address": addr("Lipová", "3", "94901", "Nitra"), "personName": person("Ján", "Vzorový")},
        {"stakeholderType": cv("Predstavenstvo", "99", "CL010113"), "statutoryBodyMember": {"value": "člen predstavenstva"},
         "validFrom": "2002-02-18", "validTo": "2005-08-01",
         "address": {"formatedAddress": "Fő utca, 54, 2900, Komárom, Maďarská republika", **addr("Fő utca", "54", "2900", "Komárom")},
         "personName": person("Eva", "Ukážková", givenFamilyNames=["Príkladná"])},
    ],
    "stakeholders": [
        {"stakeholderType": cv("Jediný akcionár a.s.", "94", "CL010109"), "validFrom": "2021-07-31",
         "address": {**addr("Kitalált út", "18", "1117", "Budapešť"), "country": cv("Maďarsko", "348", "CL000086")},
         "identifier": "Neuvedené", "fullName": "Fiktív Holding Nyrt.", "establishment": "1800-01-01"},
        {"stakeholderType": cv("Prokurista", "24", "CL010109"), "validFrom": "2002-02-18", "validTo": "2010-01-01",
         "address": addr("Panská", "2", "81101", "Bratislava"), "personName": person("Peter", "Široký")},
    ],
    "authorizations": [{"value": "Za spoločnosť konajú dvaja členovia predstavenstva spoločne.", "validFrom": "1995-03-01"}],
    "equities": [
        {"validFrom": "2009-04-02", "value": 684716057.6, "valuePaid": 684716057.6, "currency": EUR},
        {"validFrom": "1995-03-01", "validTo": "2009-04-01", "value": 20625229000.0, "currency": cv("slovenská koruna", "SKK", "CL000083")},
    ],
    "shares": [{"validFrom": "2009-04-02", "shareType": cv("Neurčené", "0", "CL010111"), "shareForm": "Akcia na doručiteľa",
                "shareNominalValue": 33.2, "shareCurrency": EUR, "amountOfFunds": 20625229}],
    "otherLegalFacts": [{"value": "Zlúčenie: spoločnosť je právnym nástupcom", "validFrom": "2013-01-01"}],
    "predecessors": [{"identifier": "07654321", "fullName": "Fiktívna Petrochémia, s.r.o.",
                      "address": addr("Vymyslená", "1", "82412", "Bratislava"), "validFrom": "2013-01-01"}],
    "statisticalCodes": {"statCodesActualization": "2026-01-01",
                         "mainActivity": cv("Výroba rafinovaných ropných produktov", "1920", "CL010579"),
                         "esa2010": cv("Nefinančné korporácie pod zahraničnou kontrolou", "11003", "CL010010")},
    "license": "CC BY 4.0",
}
# A dissolved s.r.o.: RPO ends some records the day before dissolution and leaves others open.
DISSOLVED = {
    "id": 700002,
    "identifiers": [{"value": "07654321", "validFrom": "1999-01-01"}],
    "fullNames": [{"value": "Fiktívna Petrochémia, s.r.o.", "validFrom": "1999-01-01"}],
    "addresses": [{"validFrom": "1999-01-01", **addr("Vymyslená", "1", "82412", "Bratislava")}],
    "establishment": "1999-01-01",
    "termination": "2013-01-01",
    "legalForms": [{"value": cv("Spoločnosť s ručením obmedzeným", "112", "CL000056"), "validFrom": "1999-01-01"}],
    "legalStatuses": [{"value": cv("Zaniknutá spoločnosť", "17", "CL010108"), "validFrom": "2013-01-01"}],
    "statutoryBodies": [
        {"stakeholderType": cv("Konateľ", "3", "CL010113"), "validFrom": "2005-01-01",
         "address": addr("Lipová", "3", "94901", "Nitra"), "personName": person("Ján", "Vzorový")},
        {"stakeholderType": cv("Konateľ", "3", "CL010113"), "validFrom": "1999-01-01", "validTo": "2004-12-31",
         "address": addr("Lipová", "3", "94901", "Nitra"), "personName": person("Mária", "Vzorová")},
    ],
    "stakeholders": [
        {"stakeholderType": cv("Spoločník v.o.s. / s.r.o.", "99", "CL010109"), "validFrom": "1999-01-01", "validTo": "2012-12-31",
         "address": addr("Vymyslená", "1", "82412", "Bratislava"), "identifier": "01234567", "fullName": "FIKTÍVNA RAFINÉRIA, a.s."},
    ],
    "deposits": [{"validFrom": "1999-01-01", "fullName": "FIKTÍVNA RAFINÉRIA, a.s.", "type": "Peňažný vklad", "amount": 6640.0, "currency": EUR}],
    "successors": [{"identifier": "01234567", "fullName": "FIKTÍVNA RAFINÉRIA, a.s.", "validFrom": "2013-01-01",
                    "address": addr("Vymyslená", "1", "82412", "Bratislava")}],
}
# Matches "Vzorový" + "Ján" on the server only because two different people carry the two names.
NAMESAKE = {
    "id": 700003,
    "identifiers": [{"value": "11223344", "validFrom": "2010-01-01"}],
    "fullNames": [{"value": "Iná Firma s.r.o.", "validFrom": "2010-01-01"}],
    "addresses": [{"validFrom": "2010-01-01", **addr("Krátka", "5", "01001", "Žilina")}],
    "establishment": "2010-01-01",
    "statutoryBodies": [
        {"stakeholderType": cv("Konateľ", "3", "CL010113"), "validFrom": "2010-01-01",
         "address": addr("Krátka", "5", "01001", "Žilina"), "personName": person("Ján", "Novák")},
        {"stakeholderType": cv("Konateľ", "3", "CL010113"), "validFrom": "2010-01-01",
         "address": addr("Krátka", "5", "01001", "Žilina"), "personName": person("Mária", "Vzorová-Vzorový")},
    ],
}
ENTITIES = {700001: ENTITY, 700002: DISSOLVED, 700003: NAMESAKE}


def search_result(rec):
    keys = ("id", "identifiers", "fullNames", "addresses", "establishment", "termination", "sourceRegister")
    return {k: rec[k] for k in keys if k in rec}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def api(monkeypatch):
    """Route requests to canned responses; records every request's path and params."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/rpo/v1/")
        params = dict(request.url.params)
        seen.append((path, params))
        if path == "search":
            active = params.get("onlyActive") == "true"
            pool = [r for r in ENTITIES.values() if not (active and r.get("termination"))]
            if "identifier" in params:
                pool = [r for r in pool if r["identifiers"][0]["value"] == params["identifier"]]
            elif "fullName" in params:
                pool = [r for r in pool if r["id"] in (700001, 700002)]
            elif "statutoryBodyFamilyName" in params:
                pool = [r for r in pool if r["id"] in (700001, 700002, 700003)]
            elif "stakeholderPersonFamilyName" in params:
                pool = [r for r in pool if r["id"] == 700001]
            return httpx.Response(200, json={"results": [search_result(r) for r in pool], "license": "CC BY 4.0"})
        if path.startswith("entity/"):
            rec = ENTITIES.get(int(path.split("/")[1]))
            if rec is None:
                return httpx.Response(404, json={"message": "No record found"})
            return httpx.Response(200, json={**rec, "license": "CC BY 4.0"})
        return httpx.Response(400, text=f"unexpected {path}")

    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    monkeypatch.setattr(tools, "_client", client_mod.RPOClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    return seen


def test_organization_current_state():
    org = parse.organization(ENTITY, on="2026-10-12")

    assert org["rpo_id"] == 700001
    assert org["ico"] == "01234567"
    assert org["name"] == "FIKTÍVNA RAFINÉRIA, a.s."
    assert org["former_names"] == [{"name": "Fiktívna Rafinéria a.s.", "valid_from": "1995-03-01", "valid_to": "2008-05-29"}]
    assert org["address"] == "Vymyslená 1, 82412 Bratislava"
    assert org["legal_form"] == "Akciová spoločnosť"
    assert org["source_register"] == {"name": "Obchodný register", "office": "Mestský súd Bratislava III", "number": "Sa/9999/B"}
    assert org["statutory_bodies"] == [{
        "role": "Predstavenstvo", "function": "predseda predstavenstva", "name": "Ján Vzorový",
        "address": "Lipová 3, 94901 Nitra", "valid_from": "2015-01-01", "valid_to": None,
    }]
    assert org["former_statutory_bodies"] == 1
    # A foreign company: unknown IČO and placeholder founding date are dropped, country is named.
    assert org["stakeholders"] == [{
        "role": "Jediný akcionár a.s.", "name": "Fiktív Holding Nyrt.", "ico": None,
        "address": "Kitalált út 18, 1117 Budapešť, Maďarsko", "valid_from": "2021-07-31", "valid_to": None,
    }]
    assert org["equity"] == [{"registered": "684 716 057.60 EUR", "paid": "684 716 057.60 EUR", "valid_from": "2009-04-02", "valid_to": None}]
    assert org["shares"][0]["count"] == 20625229
    assert org["activities"] == ["výroba fiktívnych palív"]
    assert org["predecessors"][0]["ico"] == "07654321"
    assert org["main_activity"] == "Výroba rafinovaných ropných produktov"
    assert "address_history" not in org


def test_organization_history():
    org = parse.organization(ENTITY, include_history=True, on="2026-10-12")

    # Former entries are listed apart, so a former director never looks current.
    assert [b["name"] for b in org["statutory_bodies"]] == ["Ján Vzorový"]
    former = org["former_statutory_bodies"][0]
    assert former["name"] == "Eva Ukážková"
    assert former["birth_surname"] == "Príkladná"
    assert former["address"] == "Fő utca, 54, 2900, Komárom, Maďarská republika"
    assert [s["name"] for s in org["stakeholders"]] == ["Fiktív Holding Nyrt."]
    assert [s["name"] for s in org["former_stakeholders"]] == ["Peter Široký"]
    assert [a["address"] for a in org["address_history"]] == ["Stará 7, 82412 Bratislava", "Vymyslená 1, 82412 Bratislava"]
    assert len(org["equity"]) == 1 and len(org["former_equity"]) == 1


def test_dissolved_organization_is_shown_on_its_last_day():
    org = parse.organization(DISSOLVED, on="2026-10-12")

    assert org["terminated"] == "2013-01-01"
    assert org["shown_as_of"] == "2012-12-31"
    assert org["legal_status"] == ["Zaniknutá spoločnosť"]
    assert [b["name"] for b in org["statutory_bodies"]] == ["Ján Vzorový"]
    assert org["former_statutory_bodies"] == 1
    assert org["stakeholders"][0]["ico"] == "01234567"
    assert org["deposits"] == [{"owner": "FIKTÍVNA RAFINÉRIA, a.s.", "amount": "6 640 EUR", "type": "Peňažný vklad", "valid_from": "1999-01-01", "valid_to": None}]
    assert org["successors"][0]["name"] == "FIKTÍVNA RAFINÉRIA, a.s."


def test_person_name_from_parts():
    assert parse.person_name({"givenNames": ["Anna", "Mária"], "familyNames": ["Vzorová"],
                              "prefixes": [cv("Ing.")], "postfixes": [cv("PhD.")]}) == "Ing. Anna Mária Vzorová, PhD."


@pytest.mark.parametrize("ico, expected", [("31 322 832", "31322832"), ("3328", "00003328"), ("00003328", "00003328")])
def test_normalize_ico(ico, expected):
    assert tools.normalize_ico(ico) == expected


@pytest.mark.parametrize("ico", ["SK123", "123456789"])
def test_normalize_ico_rejects_garbage(ico):
    with pytest.raises(ValueError):
        tools.normalize_ico(ico)


@pytest.mark.anyio
async def test_get_organization_by_short_ico(api):
    result = await tools.rpo_get_organization(ico="7654321")

    assert [o["rpo_id"] for o in result["organizations"]] == [700002]
    assert ("search", {"identifier": "07654321"}) in api
    assert ("entity/700002", {"showHistoricalData": "true"}) in api


@pytest.mark.anyio
async def test_get_organization_errors(api):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="No organization with IČO 99999999"):
        await tools.rpo_get_organization(ico="99999999")
    with pytest.raises(ToolError, match="Not found in RPO"):
        await tools.rpo_get_organization(rpo_id=1)
    with pytest.raises(ToolError, match="exactly one"):
        await tools.rpo_get_organization()


@pytest.mark.anyio
async def test_search_organization(api):
    result = await tools.rpo_search_organization(name="rafineria")

    assert result["matching"] == 1
    assert result["organizations"][0]["ico"] == "01234567"
    assert api[-1] == ("search", {"fullName": "rafineria", "onlyActive": "true"})


@pytest.mark.anyio
async def test_search_organization_flags_former_name(api):
    result = await tools.rpo_search_organization(name="petrochemia", include_terminated=True)

    names = [(o["name"], o.get("matched_former_name")) for o in result["organizations"]]
    # Active first; the dissolved company matches by its current name.
    assert names == [("FIKTÍVNA RAFINÉRIA, a.s.", None), ("Fiktívna Petrochémia, s.r.o.", None)]
    assert "onlyActive" not in api[-1][1]


@pytest.mark.anyio
async def test_search_person_verifies_namesakes_locally(api):
    result = await tools.rpo_search_person(surname="Vzorovy", first_name="jan", include_terminated=True)

    assert result["candidates"] == 3
    assert result["checked"] == 3
    # Iná Firma has a Ján and a Vzorová-Vzorový, but not one person with both names.
    assert [r["organization"]["ico"] for r in result["results"]] == ["01234567", "07654321"]
    # Ján Vzorový is also in the (mocked) register of disqualified persons.
    assert result["results"][1]["roles"][0].pop("disqualified")[0]["record_id"] == "diskvalifikacia_42"
    assert result["results"][1]["roles"][0] == {
        "kind": "statutory_body", "role": "Konateľ", "name": "Ján Vzorový",
        "address": "Lipová 3, 94901 Nitra", "valid_from": "2005-01-01", "valid_to": None,
    }
    assert ("search", {"statutoryBodyFamilyName": "Vzorovy", "statutoryBodyGivenName": "jan"}) in api


@pytest.mark.anyio
async def test_search_person_former_roles(api):
    current = await tools.rpo_search_person(surname="široky", role="stakeholder")
    former = await tools.rpo_search_person(surname="široky", role="stakeholder", include_former=True)

    assert current["results"] == []
    assert current["former_roles_only"] == 1
    assert former["results"][0]["roles"][0]["role"] == "Prokurista"
    assert former["results"][0]["roles"][0]["valid_to"] == "2010-01-01"


@pytest.mark.anyio
async def test_server_error_is_reported(monkeypatch):
    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    c = client_mod.RPOClient(http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    with pytest.raises(SourceUnavailable):
        await c.search(identifier="01234567")


@pytest.mark.parametrize("name, key", [
    ("Fiktívne Železiarne Horná Ves a.s.", "fiktivne zeleziarne horna ves"),
    ("FIKTÍVNE ŽELEZIARNE HORNÁ VES, spol. s r. o.", "fiktivne zeleziarne horna ves"),
    ("Fiktívne železiarne - Horná Ves, s.r.o.", "fiktivne zeleziarne horna ves"),
    ("Klub Fiktívne Železiarne Horná Ves", "klub fiktivne zeleziarne horna ves"),
])
def test_name_key(name, key):
    assert tools.name_key(name) == key


@pytest.mark.anyio
async def test_search_organization_ranks_by_relevance(monkeypatch):
    def org(rec_id, *names, terminated=None):
        """An organization with names oldest first; the last one is current."""
        rec = {"id": rec_id, "identifiers": [{"value": f"{rec_id:08d}", "validFrom": "2000-01-01"}],
               "fullNames": [{"value": n, "validFrom": f"{2000 + i}-01-01", **({"validTo": f"{2000 + i}-12-31"} if i < len(names) - 1 else {})}
                             for i, n in enumerate(names)],
               "establishment": "2000-01-01"}
        return {**rec, "termination": terminated} if terminated else rec

    results = [
        org(1, "Nadácia Fiktívne Železiarne Horná Ves"),
        org(2, "Fiktívne železiarne Horná Ves - servis, s.r.o."),
        org(3, "Agrofarma, s.r.o."),  # found by the server for some other reason
        org(4, "Fiktívne Železiarne Horná Ves, a.s.", "Nový názov a.s.", terminated="2010-01-01"),
        org(5, "Fiktívne Železiarne Horná Ves, a.s."),
        org(6, "Fiktívne Železiarne Horná Ves, š.p.", "Bývalý podnik, a.s."),
        org(7, "Fiktívne Železiarne Horná Ves, a.s.", "Abeceda s.r.o."),
    ]

    def handler(request):
        return httpx.Response(200, json={"results": results, "license": "CC BY 4.0"})

    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    monkeypatch.setattr(tools, "_client", client_mod.RPOClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    result = await tools.rpo_search_organization(name="fiktivne zeleziarne horna ves", include_terminated=True)

    # Active: exact name, starts with, contains, former names only (alphabetically), no visible match;
    # then the dissolved company, although its former name was an exact match.
    assert [o["rpo_id"] for o in result["organizations"]] == [5, 2, 1, 7, 6, 3, 4]
    assert result["organizations"][3]["matched_former_name"] == "Fiktívne Železiarne Horná Ves, a.s."
    assert result["organizations"][4]["matched_former_name"] == "Fiktívne Železiarne Horná Ves, š.p."


# The register of disqualified persons, checked by rpo_get_organization and rpo_search_person.
REGISTER = [{"registreGuid": "diskvalifikacia_42", "meno": "Ján Vzorový", "adresa": "Lipová 3, 94901 Nitra", "datumRozhodnutia": "01.02.2024"}]
REGISTER_DETAIL = {"registreGuid": "diskvalifikacia_42", "meno": "Ján Vzorový", "adresa": "Lipová 3, 94901 Nitra",
                   "datumRozhodnutia": "01.02.2024", "spisovaZnacka": "1T 1/2024", "vylucenieOd": "01.02.2024", "vylucenieDo": "01.02.2099"}


@pytest.fixture(autouse=True)
def register(monkeypatch, tmp_path):
    """Keep the disqualification check offline, with its index in a temporary cache."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/diskvalifikacia"):
            return httpx.Response(200, json={"numFound": 1, "diskvalifikaciaList": REGISTER})
        if request.url.path.endswith("/diskvalifikacia_42"):
            return httpx.Response(200, json=REGISTER_DETAIL)
        return httpx.Response(404, json={"message": "not found"})

    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(court_client, "INTERVAL", 0)
    monkeypatch.setattr(court_tools, "_client", court_client.CourtClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))


@pytest.mark.anyio
async def test_get_organization_flags_disqualified_director(api):
    org = (await tools.rpo_get_organization(ico="1234567"))["organizations"][0]

    director = org["statutory_bodies"][0]
    assert director["name"] == "Ján Vzorový"
    assert director["disqualified"] == [{
        "record_id": "diskvalifikacia_42", "matched_by": "name and address", "name_match": "exact",
        "register_name": "Ján Vzorový", "register_address": "Lipová 3, 94901 Nitra", "address_comparison": "same address",
        "ban_from": "2024-02-01", "ban_to": "2099-02-01", "ban_active": True, "decision_date": "2024-02-01", "court": None,
    }]
    assert all("disqualified" not in p for p in org["statutory_bodies"][1:] + org["stakeholders"])


def test_entries_the_register_recorded_twice_are_shown_once():
    from sk_public_money import history

    director = {"role": "Konateľ", "name": "JUDr. Fiktívny Advokát", "valid_from": "2019-03-19"}
    items = [
        {**director, "valid_to": "2023-11-13"},
        {**director, "valid_to": "2023-11-09"},  # the same ending, recorded twice
        {"role": "Konateľ", "name": "JUDr. Nová Konateľka", "valid_from": "2022-03-29", "valid_to": None},
        {"role": "Konateľ", "name": "JUDr. Nová Konateľka", "valid_from": "2022-03-29", "valid_to": None},
    ]
    ended = [True, True, False, False]
    current, former = history.split(items, ended, include_history=True)
    assert [c["name"] for c in current] == ["JUDr. Nová Konateľka"]
    assert former == [{**director, "valid_to": "2023-11-13"}]
    assert history.split(items, ended, include_history=False) == (current, 1)
