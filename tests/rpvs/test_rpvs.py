"""RPVS tests against a mock of the OData API. All people and companies here are fictional;
the record shapes are copied from real rpvs.gov.sk/opendatav2 responses."""

import httpx
import pytest

from sk_public_money.sources.rpvs import client as client_mod
from sk_public_money.sources.rpvs import parse, tools


@pytest.fixture
def anyio_backend():
    return "asyncio"


def identity(rec_id, partner_id, name, ico, valid_from, valid_to=None, *, vlozka=None):
    return {
        "Id": rec_id, "Meno": None, "Priezvisko": None, "DatumNarodenia": None, "TitulPred": None, "TitulZa": None,
        "ObchodneMeno": name, "Ico": ico, "FormaOsoby": "PravnickaOsoba",
        "PlatnostOd": f"{valid_from}T00:00:00+01:00", "PlatnostDo": f"{valid_to}T23:59:59.9+02:00" if valid_to else None,
        "Partner": {"Id": partner_id, "CisloVlozky": vlozka or partner_id},
        "Adresa": {"Id": 1, "MenoUlice": "Hlavná", "OrientacneCislo": "12", "SupisneCislo": "345", "Mesto": "Nitra", "MestoKod": None, "Psc": "94901", "Identifikator": None},
        "PravnaForma": {"Id": 1, "Meno": "Akciová spoločnosť", "StatistickyKod": "121", "PlatnostOd": None, "PlatnostDo": None},
    }


def kuv(rec_id, first, last, born, valid_from, valid_to=None, *, official=False, partner_id=500):
    return {
        "Id": rec_id, "Meno": first + " ", "Priezvisko": last, "DatumNarodenia": f"{born}T00:00:00+01:00",
        "TitulPred": "Ing.", "TitulZa": "", "JeVerejnyCinitel": official, "ObchodneMeno": None, "Ico": None,
        "PlatnostOd": f"{valid_from}T00:00:00+01:00", "PlatnostDo": f"{valid_to}T00:00:00+01:00" if valid_to else None,
        "Partner": {"Id": partner_id, "CisloVlozky": 498},
    }


IDENTITIES = [
    identity(1, 500, "Fiktívna Rafinéria, a.s.", "01234567", "2017-02-01", "2017-07-31", vlozka=498),
    identity(2, 500, "FIKTÍVNA RAFINÉRIA, a.s.", "01234567", "2017-08-01", vlozka=498),
]
PARTNER = {
    "Id": 500,
    "CisloVlozky": 498,
    "KonecniUzivateliaVyhod": [
        {k: v for k, v in kuv(10, "Ján", "Vzorový", 1970, "2018-01-01").items() if k != "Partner"},
        {k: v for k, v in kuv(11, "Eva", "Ukážková", 1965, "2017-08-01", "2019-12-31", official=True).items() if k != "Partner"},
    ],
    "OpravneneOsoby": [{
        "Id": 3, "Meno": None, "Priezvisko": None, "DatumNarodenia": None, "TitulPred": None, "TitulZa": None,
        "ObchodneMeno": "Advokátska kancelária Príklad s.r.o.", "Ico": "76543210", "FormaOsoby": "PravnickaOsoba",
        "PlatnostOd": "2017-08-01T00:00:00+02:00", "PlatnostDo": None,
    }],
    "VerejniFunkcionari": [],
    "Vymaz": None,
    "Pokuta": None,
    "KvalifikovanePodnety": [],
    "OverenieIdentifikacieKUV": {"Id": 9, "PartnerId": 0, "TypOvereniaIdentifikacieKUVId": 4, "Platny": True,
                                 "DatumVytvorenia": "2026-07-23T13:30:04+02:00", "DatumOverenia": "2026-07-16T00:00:00+02:00"},
}


def page(rows, count=None):
    body = {"value": rows}
    if count is not None:
        body["@odata.count"] = count
    return httpx.Response(200, json=body)


@pytest.fixture
def api(monkeypatch):
    """Route requests to canned responses; records every $filter seen."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/opendatav2/")
        flt = request.url.params.get("$filter", "")
        seen.append((path, flt))
        if path == "PartneriVerejnehoSektora" and flt.startswith("Ico eq"):
            return page(IDENTITIES if "'01234567'" in flt else [], 2)
        if path == "PartneriVerejnehoSektora" and flt.startswith("Partner/Id in"):
            return page(IDENTITIES, 2)
        if path == "PartneriVerejnehoSektora" and flt.startswith("(contains("):
            return page(IDENTITIES[1:], 1)
        if path == "Partneri(500)":
            return httpx.Response(200, json=PARTNER)
        if path == "KonecniUzivateliaVyhod":
            return page([kuv(10, "Ján", "Vzorový", 1970, "2018-01-01")], 1)
        if path in ("VerejniFunkcionari", "OpravneneOsoby"):
            return page([], 0)
        if path == "Partneri(999)":
            return httpx.Response(404)
        return httpx.Response(400, json={"error": {"message": f"unexpected {path} {flt}"}})

    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    monkeypatch.setattr(tools, "_client", client_mod.RPVSClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    return seen


def test_partner_shape():
    p = parse.partner(IDENTITIES, PARTNER)

    assert p["partner_id"] == 500
    assert p["url"] == "https://rpvs.gov.sk/rpvs/Partner/Partner/Detail/498"
    assert p["name"] == "FIKTÍVNA RAFINÉRIA, a.s."
    assert p["address"] == "Hlavná 345/12, 94901 Nitra"
    assert p["legal_form"] == "Akciová spoločnosť"
    assert p["registered"] is True
    assert p["beneficial_owners"] == [{
        "name": "Ing. Ján Vzorový", "birth_year": 1970, "public_official": False,
        "valid_from": "2018-01-01", "valid_to": None,
    }]
    assert p["former_beneficial_owners"] == 1
    assert p["authorized_persons"][0]["name"] == "Advokátska kancelária Príklad s.r.o."
    assert p["owners_verified"] == {"date": "2026-07-16", "valid": True}
    assert "identity_history" not in p


def test_partner_history_and_deletion():
    deleted = {**PARTNER, "Vymaz": {"Id": 1, "Dovod": "ExOffo", "Poznamka": "Hromadný výmaz", "Datum": "2020-01-31T00:00:00+01:00"}}
    p = parse.partner(IDENTITIES, deleted, include_history=True)

    assert p["registered"] is False
    assert p["deletion"] == {"date": "2020-01-31", "reason": "ex officio", "note": "Hromadný výmaz"}
    assert [o["name"] for o in p["beneficial_owners"]] == ["Ing. Ján Vzorový", "Ing. Eva Ukážková"]
    assert p["beneficial_owners"][1]["public_official"] is True
    assert [i["name"] for i in p["identity_history"]] == ["Fiktívna Rafinéria, a.s.", "FIKTÍVNA RAFINÉRIA, a.s."]


def test_birth_date_is_reduced_to_year():
    owner = parse.beneficial_owner(kuv(1, "Ján", "Vzorový", 1970, "2018-01-01"))
    assert owner["birth_year"] == 1970
    assert "1970-01-01" not in str(owner)


@pytest.mark.parametrize("ico, expected", [
    ("1234567", "Ico eq '01234567' or Ico eq '1234567'"),
    ("31 322 832", "Ico eq '31322832'"),
    ("HRA1980", "Ico eq 'HRA1980'"),
])
def test_ico_filter(ico, expected):
    assert tools._ico_filter(ico) == expected


def test_odata_quoting():
    assert client_mod.odata_str("O'Neil") == "'O''Neil'"


@pytest.mark.anyio
async def test_get_partner_by_ico(api):
    result = await tools.rpvs_get_partner(ico="1234567")

    assert [p["partner_id"] for p in result["partners"]] == [500]
    assert result["partners"][0]["ico"] == "01234567"
    assert ("PartneriVerejnehoSektora", "Ico eq '01234567' or Ico eq '1234567'") in api


@pytest.mark.anyio
async def test_get_partner_unknown_ico_is_reported(api):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="No partner with IČO 99999999"):
        await tools.rpvs_get_partner(ico="99999999")
    with pytest.raises(ToolError, match="exactly one"):
        await tools.rpvs_get_partner()


@pytest.mark.anyio
async def test_search_person(api):
    result = await tools.rpvs_search_person(surname="Vzorový", first_name="Ján")

    assert result["matching"] == {"beneficial_owner": 1, "public_official": 0, "authorized_person": 0}
    hit = result["results"][0]
    assert hit["role"] == "beneficial_owner"
    assert hit["name"] == "Ing. Ján Vzorový"
    assert hit["partner"]["ico"] == "01234567"
    assert hit["partner"]["url"] == "https://rpvs.gov.sk/rpvs/Partner/Partner/Detail/498"
    kuv_filter = next(f for p, f in api if p == "KonecniUzivateliaVyhod")
    assert kuv_filter == "tolower(Priezvisko) eq 'vzorový' and startswith(tolower(Meno),'ján') and PlatnostDo eq null"


@pytest.mark.anyio
async def test_search_partner(api):
    result = await tools.rpvs_search_partner(name="Rafinéria")

    assert result["partners"][0]["partner_id"] == 500
    assert result["partners"][0]["registered"] is True
    flt = next(f for p, f in api if f.startswith("(contains("))
    assert flt == "(contains(tolower(ObchodneMeno),'rafinéria') or contains(tolower(Priezvisko),'rafinéria')) and PlatnostDo eq null"
