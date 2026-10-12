from pathlib import Path

import pytest

from sk_public_money.sources.crz import export, parse

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_search_results():
    result = parse.parse_search(fixture("search_slovnaft.html"))

    assert result["page"] == 0
    assert result["has_more"] is True
    assert len(result["results"]) == 20
    assert result["results"][0] == {
        "id": 12920842,
        "url": "https://www.crz.gov.sk/zmluva/12920842/",
        "published": "2026-10-12",
        "title": "Rámcová zmluva o poskytovaní podporných služieb a dodávke regulačnej elektriny na rok 2027",
        "number": "2026-0445-1215320",
        "amount_eur": 0.0,
        "supplier": "SLOVNAFT, a.s.",
        "buyer": "Slovenská elektrizačná prenosová sústava, a.s.",
    }


def test_departments_from_search_form():
    departments = parse.parse_department_options(fixture("search_slovnaft.html"))

    assert {"id": 114573, "name": "Ministerstvo dopravy a výstavby SR"} in departments
    assert all(d["id"] != 0 for d in departments)


def test_contract_with_amendment():
    c = parse.parse_contract(fixture("contract_3070264.html"), url="https://www.crz.gov.sk/zmluva/3070264/")

    assert c["id"] == 3070264
    assert c["kind"] == "contract"
    assert c["title"] == "Zmluva o zriadení vecného bremena MPV - 11995/6253/2017"
    assert c["number"] == "2134/6253/2017"
    assert c["department"] == "Ministerstvo dopravy a výstavby SR"
    assert c["buyer"] == {
        "name": "Slovenská správa ciest, Bratislava",
        "address": "Miletičova 19 , P.O.BOX 19, 826 19 Bratislava",
        "ico": "003328",
    }
    assert c["supplier"] == {"name": "SLOVNAFT a.s.", "address": "Vlčie hrdlo 1, Bratislava"}
    assert c["dates"] == {
        "published": "2017-08-22",
        "concluded": "2017-08-15",
        "effective": "2017-08-23",
        "valid_until": None,
    }
    assert c["amounts_eur"] == {"agreed": 2148.04, "total": 2148.04}
    assert c["attachments"][0]["kind"] == "sken"
    assert c["attachments"][0]["url"] == "https://www.crz.gov.sk/data/att/3070266_dokument."
    assert c["amendments"] == [{
        "id": 8818627,
        "url": "https://www.crz.gov.sk/8818627-sk/dodatok-c1/",
        "published": "2024-01-15",
        "title": "Dodatok č.1 k Zmluve o zriadení vecného bremena v prospech tretej osoby č. 2134/6253/2017",
        "number": "Dodatok č.1",
        "amount_eur": 0.0,
        "supplier": "SLOVNAFT a.s.",
        "buyer": "Slovenská správa ciest, Bratislava",
    }]
    assert "parent_contract_id" not in c


def test_contract_with_description_and_no_attachments():
    c = parse.parse_contract(fixture("contract_12920842.html"))

    assert c["id"] == 12920842
    assert c["description"] == "Stanovenie podmienok zmluvy"
    assert c["attachments"] == []


def test_amendment():
    a = parse.parse_contract(fixture("amendment_8818627.html"), url="https://www.crz.gov.sk/8818627-sk/dodatok-c1/")

    assert a["id"] == 8818627
    assert a["kind"] == "amendment"
    assert a["number"] == "Dodatok č.1"
    assert a["buyer"] == {"name": "Slovenská správa ciest, Bratislava"}
    assert a["dates"]["published"] == "2024-01-15T14:32"
    assert a["dates"]["signed"] is None
    assert a["amounts_eur"] == {"amount": 0.0}
    assert a["attachments"] == [{
        "name": "Dodatok č.1 k Zmluve o zriadení vecného bremena v prospech tretej osoby č. 2134/6253/2017",
        "kind": "text",
        "url": "https://www.crz.gov.sk/data/att/4689797.pdf",
        "format": "pdf",
        "size": "312.86 kB",
    }]
    assert a["parent_contract_id"] == 3070264


def test_not_found_page():
    html = '<html><body><main><h1 class="page__title">Vybranú stránku nie je možné zobraziť</h1></main></body></html>'
    with pytest.raises(parse.NotFound):
        parse.parse_contract(html)


@pytest.mark.parametrize("value, expected", [
    ("2 148,04 €", 2148.04),
    ("0,00\xa0€", 0.0),
    ("92 890 743,84 €", 92890743.84),
    ("", None),
])
def test_parse_amount(value, expected):
    assert parse.parse_amount(value) == expected


def test_export():
    records = export.parse_export_zip((FIXTURES / "2024-01-15.zip").read_bytes())

    assert [r["id"] for r in records] == [3070264, 4328425]
    r = records[0]
    assert r["url"] == "https://www.crz.gov.sk/index.php?ID=3070264"
    assert r["buyer"] == {
        "name": "Slovenská správa ciest, Bratislava",
        "ico": "003328",
        "address": "Miletičova 19 , P.O.BOX 19, 826 19 Bratislava",
    }
    assert r["supplier"]["name"] == "SLOVNAFT a.s."
    assert r["department_id"] == 114573
    assert r["amounts_eur"] == {"agreed": 2148.04, "total": 2148.04}
    assert r["dates"]["valid_until"] is None
    assert r["dates"]["published"] == "2017-08-22T13:40:24"
    assert r["attachments"] == [{
        "name": "2134_1729_6253_2017_Slovnaft_Mesto_ZV_SSC_gp_41_14_z",
        "kind": "sken",
        "url": "https://www.crz.gov.sk/data/att/3070266_dokument.",
        "size_bytes": 193007,
    }]
    assert r["confirmation_url"] == "https://www.crz.gov.sk/data/att/3070264_potvrdenie.pdf"


def test_export_filters():
    records = export.parse_export_zip((FIXTURES / "2024-01-15.zip").read_bytes())

    assert [r["id"] for r in records if export.matches(r, text="slovnaft")] == [3070264]
    assert [r["id"] for r in records if export.matches(r, ico="3328")] == [3070264]
    assert [r["id"] for r in records if export.matches(r, ico="50349287")] == [4328425]
    assert [r["id"] for r in records if export.matches(r, department_id=356772)] == [4328425]


def test_other_portal_page_is_not_a_contract():
    with pytest.raises(parse.NotFound):
        parse.parse_contract(fixture("search_slovnaft.html"))
