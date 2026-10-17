"""REPLIK tests against a mock of its SOAP API. All companies and people here are fictional; the
XML shapes are copied from real replik-ws.justice.sk responses."""

import re
from datetime import date, timedelta
from xml.sax.saxutils import escape

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sk_public_money.sources.insolvency import client as client_mod
from sk_public_money.sources.insolvency import parse, tools


@pytest.fixture
def anyio_backend():
    return "asyncio"


K = "datatypes.konanie.verejnost.ru.sk.hp.com"
O = "datatypes.oznam.verejnost.ru.sk.hp.com"


def envelope(ns: str, body: str) -> str:
    return ('<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Header/>'
            f'<SOAP-ENV:Body>{body.replace("<x:", "<ns2:").replace("</x:", "</ns2:").replace("xmlns:x=", "xmlns:ns2=")}'
            '</SOAP-ENV:Body></SOAP-ENV:Envelope>')


INFO_COMPANY = f"""<x:KonanieInfo><x:Id>5001</x:Id><x:SpisovaZnackaSudu>B1-31K/7/2021</x:SpisovaZnackaSudu>
<x:Typ>KONKURZ</x:Typ><x:DatumZacatiaKonania>2021-05-28</x:DatumZacatiaKonania><x:DatumZacatiaProcesu>2021-06-18</x:DatumZacatiaProcesu>
<x:DatumPodania>2021-03-26</x:DatumPodania><x:Dlznik>Fiktívna Stavba, s.r.o. v likvidácii</x:Dlznik><x:DlznikIco>01234567</x:DlznikIco>
<x:DatumPoslednejUdalosti>2025-07-11</x:DatumPoslednejUdalosti><x:Sud>104</x:Sud><x:SudNazov>Mestský súd Bratislava III</x:SudNazov>
<x:Spravca>Vzorový Správca k.s.</x:Spravca><x:StavKonania>ZACATY_PROCES_KONANIA</x:StavKonania><x:PocetPopreti>3</x:PocetPopreti></x:KonanieInfo>"""

INFO_PERSON = """<x:KonanieInfo><x:Id>7002</x:Id><x:SpisovaZnackaSudu>25OdK/99/2025</x:SpisovaZnackaSudu><x:Typ>ODDLZENIE_KONKURZ</x:Typ>
<x:DatumZacatiaProcesu>2025-06-24</x:DatumZacatiaProcesu><x:DatumPodania>2025-05-30</x:DatumPodania><x:Dlznik>Ukážkový Ján</x:Dlznik>
<x:DlznikDatumNarodenia>1980-01-02</x:DlznikDatumNarodenia><x:DatumPoslednejUdalosti>2026-09-29</x:DatumPoslednejUdalosti>
<x:Sud>104</x:Sud><x:SudNazov>Mestský súd Bratislava III</x:SudNazov><x:Spravca>Príkladová Eva, JUDr.</x:Spravca>
<x:StavKonania>SKONCENY_PROCES</x:StavKonania><x:DatumUkonceniaProcesu>2026-09-29</x:DatumUkonceniaProcesu><x:PocetPopreti>0</x:PocetPopreti></x:KonanieInfo>"""

DETAIL_COMPANY = """<x:Konanie><x:Id>5001</x:Id><x:SpisovaZnackaSudu>B1-31K/7/2021</x:SpisovaZnackaSudu><x:Typ>KONKURZ</x:Typ>
<x:Navrhovatel xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="x:PravnickaOsoba"><x:Adresa><x:Ulica>Hlavná 1</x:Ulica>
<x:SupisneCislo/><x:Obec>Bratislava</x:Obec><x:Psc>85000</x:Psc><x:Krajina>SVK</x:Krajina></x:Adresa><x:Email/>
<x:ObchodneMeno>Daňový úrad</x:ObchodneMeno><x:Ico/><x:PravnaForma/></x:Navrhovatel>
<x:Dlznik xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="x:PravnickaOsoba"><x:Adresa><x:Ulica>Skúšobná</x:Ulica>
<x:SupisneCislo>50</x:SupisneCislo><x:OrientacneCislo>0</x:OrientacneCislo><x:Obec>Bratislava - mestská časť Ružinov</x:Obec>
<x:Psc>82101</x:Psc><x:Krajina>SVK</x:Krajina></x:Adresa><x:Email/><x:ObchodneMeno>Fiktívna Stavba, s.r.o. v likvidácii</x:ObchodneMeno>
<x:Ico>01234567</x:Ico></x:Dlznik><x:Sud><x:id>104</x:id><x:nazov>Mestský súd Bratislava III</x:nazov></x:Sud>
<x:Sudca>JUDr. Peter Vymyslený</x:Sudca><x:Spravca><x:Znacka>S9999</x:Znacka><x:DatumZapisu>2015-03-03</x:DatumZapisu><x:Meno/>
<x:Priezvisko>Starý Názov Správcu, k. s.</x:Priezvisko><x:TitulPredMenom/><x:ObchodneMeno>Vzorový Správca k.s.</x:ObchodneMeno>
<x:Ico>76543210</x:Ico></x:Spravca><x:DatumZacatiaKonania>2021-05-28</x:DatumZacatiaKonania><x:DatumZacatiaProcesu>2021-06-18</x:DatumZacatiaProcesu>
<x:TypSpravcu>RIADNY</x:TypSpravcu><x:TypKonaniaPodlaUzemnejPlatnosti>Hlavné konanie</x:TypKonaniaPodlaUzemnejPlatnosti></x:Konanie>"""

DETAIL_PERSON = """<x:Konanie><x:Id>7002</x:Id><x:SpisovaZnackaSudu>25OdK/99/2025</x:SpisovaZnackaSudu><x:Typ>ODDLZENIE_KONKURZ</x:Typ>
<x:Navrhovatel xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="x:FyzickaOsoba"><x:Adresa><x:Ulica>Cvičná</x:Ulica>
<x:SupisneCislo>3121</x:SupisneCislo><x:OrientacneCislo>6</x:OrientacneCislo><x:Obec>Bratislava-Rača</x:Obec><x:Psc>83106</x:Psc>
<x:Krajina>SVK</x:Krajina></x:Adresa><x:Email/><x:Meno>Ján</x:Meno><x:Priezvisko>Ukážkový</x:Priezvisko><x:DatumNarodenia>1980-01-02</x:DatumNarodenia></x:Navrhovatel>
<x:Dlznik xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="x:FyzickaOsoba"><x:Adresa><x:Ulica>Cvičná</x:Ulica>
<x:SupisneCislo>3121</x:SupisneCislo><x:OrientacneCislo>6</x:OrientacneCislo><x:Obec>Bratislava-Rača</x:Obec><x:Psc>83106</x:Psc>
<x:Krajina>SVK</x:Krajina></x:Adresa><x:Email/><x:Meno>Ján</x:Meno><x:Priezvisko>Ukážkový</x:Priezvisko><x:DatumNarodenia>1980-01-02</x:DatumNarodenia></x:Dlznik>
<x:Sud><x:id>104</x:id><x:nazov>Mestský súd Bratislava III</x:nazov></x:Sud><x:Sudca>JUDr. Anna Fiktívna</x:Sudca>
<x:Spravca><x:Znacka>S1111</x:Znacka><x:DatumZapisu>2007-05-14</x:DatumZapisu><x:Meno>Eva</x:Meno><x:Priezvisko>Príkladová</x:Priezvisko>
<x:TitulPredMenom>JUDr.</x:TitulPredMenom><x:DatumNarodenia>1964-09-20</x:DatumNarodenia><x:ObchodneMeno>Príkladová</x:ObchodneMeno><x:Ico/></x:Spravca>
<x:DatumZacatiaProcesu>2025-06-24</x:DatumZacatiaProcesu><x:TypSpravcu>RIADNY</x:TypSpravcu></x:Konanie>"""


def notice_info(i: int, court="104", file_number="B1-31K/7/2021", kind="OZNAM_SPRAVCA", proceeding=5001) -> str:
    issued = date(2021, 5, 27) + timedelta(days=7 * i)  # REPLIK lists notices oldest first
    return (f"<x:VerejnyOznamInfo><x:OznamId>{900000 + i}</x:OznamId><x:OznamTyp>{kind}</x:OznamTyp><x:SudKod>{court}</x:SudKod>"
            f"<x:SudNazov>Mestský súd Bratislava III</x:SudNazov><x:SpisovaZnackaSudnehoSpisu>{file_number}</x:SpisovaZnackaSudnehoSpisu>"
            f"<x:KonanieId>{proceeding}</x:KonanieId><x:KonanieTyp>KONKURZ</x:KonanieTyp>"
            f"<x:DatumVydania>{issued.isoformat()}</x:DatumVydania></x:VerejnyOznamInfo>")


TRUSTEE_HTML = ("<p>Vzorov&yacute;&nbsp;Spr&aacute;vca k. s., spr&aacute;vca S 9999, spr&aacute;vca konkurznej podstaty "
                "&uacute;padcu <strong>Fikt&iacute;vna Stavba, s.r.o.</strong> v likvid&aacute;cii, IČO: 01 234 567, "
                "oznamuje, že do spisu možno nahliadať.</p>\n\n<p>IBAN:&nbsp;SK00 0000 0000 0000 0000 0000</p>")

NOTICE_TRUSTEE = f"""<x:VerejnyOznam xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="x:VerejnyOznamSpravca">
<x:OznamId>900149</x:OznamId><x:OznamTyp>OZNAM_SPRAVCA</x:OznamTyp><x:SudKod>104</x:SudKod><x:SudNazov>Mestský súd Bratislava III</x:SudNazov>
<x:SpisovaZnackaSudnehoSpisu>B1-31K/7/2021</x:SpisovaZnackaSudnehoSpisu><x:KonanieId>5001</x:KonanieId><x:KonanieTyp>KONKURZ</x:KonanieTyp>
<x:DatumVydania>2024-06-22</x:DatumVydania><x:ObsahujePrilohy>true</x:ObsahujePrilohy><x:VerejnyOznamPrilohyList><x:VerejnyOznamPriloha>
<x:Nazov>supis.pdf</x:Nazov><x:PrilohaId>42</x:PrilohaId></x:VerejnyOznamPriloha></x:VerejnyOznamPrilohyList>
<x:DruhPodania>OZNAM_O_TOM_KDE_A_KEDY_MOZNO_NAHLIADAT_DO_SPISU</x:DruhPodania><x:Text>{escape(TRUSTEE_HTML)}</x:Text>
<x:SpisovaZnackaSpravcovskehoSpisu>B1-31K/7/2021 S9999</x:SpisovaZnackaSpravcovskehoSpisu></x:VerejnyOznam>"""

NOTICE_COURT = """<x:VerejnyOznam xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="x:VerejnyOznamSud">
<x:OznamId>900000</x:OznamId><x:OznamTyp>OZNAM_SUD</x:OznamTyp><x:SudKod>102</x:SudKod><x:SudNazov>Okresný súd Bratislava I</x:SudNazov>
<x:SpisovaZnackaSudnehoSpisu>31K/7/2021</x:SpisovaZnackaSudnehoSpisu><x:KonanieId>5001</x:KonanieId><x:KonanieTyp>KONKURZ</x:KonanieTyp>
<x:DatumVydania>2021-05-27</x:DatumVydania><x:ObsahujePrilohy>false</x:ObsahujePrilohy><x:VerejnyOznamPrilohyList/>
<x:TextDruh>UZNESENIE</x:TextDruh><x:TextPoucenie>Proti tomuto uzneseniu odvolanie nie je prípustné.
</x:TextPoucenie><x:TextHlavicka>Okresný súd Bratislava I v právnej veci dlžníka: Fiktívna Stavba, s.r.o., IČO: 01 234 567,
</x:TextHlavicka><x:TextOznam/><x:TextRozhodnutie>Súd začína konkurzné konanie voči dlžníkovi.
</x:TextRozhodnutie></x:VerejnyOznam>"""


def value(body: str, tag: str) -> str | None:
    m = re.search(rf"<d:{tag}>([^<]*)</d:{tag}>", body)
    return m.group(1) if m else None


def info_list(op: str, items: list[str], total: int, ns: str, list_tag: str) -> str:
    inner = "".join(items)
    return envelope(ns, f'<x:{op}Response xmlns:x="{ns}"><x:{list_tag}>{inner}</x:{list_tag}><x:VysledkovCelkom>{total}</x:VysledkovCelkom></x:{op}Response>')


@pytest.fixture
def api(monkeypatch):
    """A mock REPLIK endpoint: dispatches on the request element; records each (operation, body)."""
    seen = []
    notices = [notice_info(i, court="104") for i in range(150)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        op = re.search(r"<d:(\w+)Request", body).group(1)
        seen.append((op, body))
        page, size = int(value(body, "Stranka") or 0), int(value(body, "VysledkovNaStranku") or 100)
        if op == "getKonaniePodlaICO":
            items = [INFO_COMPANY] if value(body, "Ico") == "01234567" else []
            return httpx.Response(200, text=info_list(op, items, len(items), K, "KonanieInfoList"))
        if op == "vyhladajKonanie":
            q = value(body, "Query")
            items = [INFO_PERSON] if q in ("Ukazkovy", "25OdK/99/2025") else []
            return httpx.Response(200, text=info_list(op, items, len(items), K, "KonanieInfoList"))
        if op == "getKonanieDetail":
            found = {"5001": DETAIL_COMPANY, "7002": DETAIL_PERSON}.get(value(body, "KonanieId"))
            if value(body, "KonanieId") == "abc":
                return httpx.Response(500, text='<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Header/><SOAP-ENV:Body><SOAP-ENV:Fault><faultcode>SOAP-ENV:Server</faultcode><faultstring xml:lang="en">\'abc\' is not number!</faultstring></SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>')
            return httpx.Response(200, text=envelope(K, f'<x:getKonanieDetailResponse xmlns:x="{K}">{found or ""}</x:getKonanieDetailResponse>'))
        if op == "getVerejneOznamyPodlaZnackyASudu":
            if value(body, "SpisovaZnackaSudnehoSpisu") == "B1-31K/7/2021" and value(body, "SudKod") == "104":
                chunk = notices[page * size:(page + 1) * size]
                return httpx.Response(200, text=info_list(op, chunk, len(notices), O, "VerejnyOznamInfoList"))
            return httpx.Response(200, text=info_list(op, [], 0, O, "VerejnyOznamInfoList"))
        if op == "getVerejneOznamyPodlaICO":
            return httpx.Response(200, text=info_list(op, [], 0, O, "VerejnyOznamInfoList"))
        if op == "getVerejnyOznamDetail":
            found = {"900149": NOTICE_TRUSTEE, "900000": NOTICE_COURT}.get(value(body, "OznamId"))
            if found is None and value(body, "OznamId").startswith("900"):
                found = NOTICE_TRUSTEE.replace("900149", value(body, "OznamId"))
            return httpx.Response(200, text=envelope(O, f'<x:getVerejnyOznamDetailResponse xmlns:x="{O}">{found or ""}</x:getVerejnyOznamDetailResponse>'))
        return httpx.Response(500, text="unexpected")

    monkeypatch.setattr(client_mod, "INTERVAL", 0)
    monkeypatch.setattr(tools, "_client", client_mod.ReplikClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    return seen


# --- parsing -----------------------------------------------------------------------------------

def test_type_codes_of_both_services_agree():
    assert parse.type_code("ODDLZENIE-KONKURZ") == parse.type_code("ODDLZENIE_KONKURZ") == "ODDLZENIE_KONKURZ"
    assert parse.type_code("ODDLZENIE-SPLATKOVYKALENDAR") == parse.type_code("ODDLZENIE_SPLATKOVY_KALENDAR")
    assert parse.type_code("INEKONANIA") == "INE"


def test_trustee_html_becomes_plain_text():
    text = parse.html_text(TRUSTEE_HTML)
    assert text.startswith("Vzorový Správca k. s., správca S 9999")
    # Inline tags stay on the line; paragraphs are separated by a blank line.
    assert "úpadcu Fiktívna Stavba, s.r.o. v likvidácii, IČO: 01 234 567" in text
    assert text.endswith("nahliadať.\n\nIBAN: SK00 0000 0000 0000 0000 0000")
    assert parse.html_text("Plain text from a court.\n") == "Plain text from a court."


# --- tools -------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_search_by_ico_with_spaces(api):
    result = await tools.insolvency_search(ico="1 234 567")
    assert value(api[0][1], "Ico") == "01234567"
    assert result["matched_by"] == "ico" and result["total"] == 1 and not result["has_more"]
    assert result["proceedings"] == [{
        "id": 5001, "file_number": "B1-31K/7/2021", "court": "Mestský súd Bratislava III", "court_code": "104",
        "type": "KONKURZ", "type_name": "konkurz (bankruptcy)", "status": "ZACATY_PROCES_KONANIA",
        "debtor": "Fiktívna Stavba, s.r.o. v likvidácii", "debtor_ico": "01234567", "filed": "2021-03-26",
        "opened": "2021-05-28", "declared": "2021-06-18", "last_event_date": "2025-07-11",
        "trustee": "Vzorový Správca k.s.", "disputed_claims": 3,
    }]


@pytest.mark.anyio
async def test_search_by_text_says_it_matched_by_name(api):
    result = await tools.insolvency_search(text="Ukazkovy")
    assert result["matched_by"] == "text" and "not proof" in result["note"]
    hit = result["proceedings"][0]
    assert hit["debtor"] == "Ukážkový Ján" and hit["debtor_birth_date"] == "1980-01-02"
    assert hit["type"] == "ODDLZENIE_KONKURZ" and hit["ended"] == "2026-09-29"
    assert "disputed_claims" not in hit  # zero is left out


@pytest.mark.anyio
async def test_search_needs_one_criterion(api):
    with pytest.raises(ToolError):
        await tools.insolvency_search()
    with pytest.raises(ToolError, match="8 digits"):
        await tools.insolvency_search(ico="123456789")


@pytest.mark.anyio
async def test_get_company_proceeding_with_newest_notices(api):
    result = await tools.insolvency_get(proceeding_id=5001, notices=5)
    p = result["proceeding"]
    assert p["debtor"] == {"kind": "legal_entity", "name": "Fiktívna Stavba, s.r.o. v likvidácii", "ico": "01234567",
                           "address": "Skúšobná 50, 82101 Bratislava - mestská časť Ružinov"}
    assert p["petitioners"] == [{"kind": "legal_entity", "name": "Daňový úrad", "address": "Hlavná 1, 85000 Bratislava"}]
    assert p["trustee"]["name"] == "Vzorový Správca k.s." and p["trustee"]["other_name"] == "Starý Názov Správcu, k. s."
    assert p["status"] == "ZACATY_PROCES_KONANIA" and p["judge"] == "JUDr. Peter Vymyslený"
    # 150 notices, listed oldest first by REPLIK: the newest five come from the last page.
    assert result["notices_total"] == 150
    assert [n["id"] for n in result["notices"]] == [900149, 900148, 900147, 900146, 900145]
    pages = [value(b, "Stranka") for op, b in api if op == "getVerejneOznamyPodlaZnackyASudu"]
    assert pages == ["0", "1"]


@pytest.mark.anyio
async def test_get_person_proceeding_publishes_birth_date(api):
    result = await tools.insolvency_get(proceeding_id=7002, notices=0)
    p = result["proceeding"]
    assert p["debtor"]["kind"] == "person" and p["debtor"]["name"] == "Ján Ukážkový"
    assert p["debtor"]["birth_date"] == "1980-01-02"
    assert p["trustee"]["name"] == "JUDr. Eva Príkladová" and p["trustee"]["birth_date"] == "1964-09-20"
    assert p["status"] == "SKONCENY_PROCES" and p["ended"] == "2026-09-29"
    assert "notices" not in result


@pytest.mark.anyio
async def test_get_with_full_text(api):
    result = await tools.insolvency_get(proceeding_id=5001, notices=2, full_text=True)
    first = result["notices"][0]
    assert first["document"] == "oznam o tom kde a kedy mozno nahliadat do spisu"
    assert first["text"].startswith("Vzorový Správca k. s.")
    assert first["attachments"] == [{"id": 42, "name": "supis.pdf"}]


@pytest.mark.anyio
async def test_get_one_court_notice(api):
    result = await tools.insolvency_get(notice_id=900000)
    n = result["notice"]
    assert n["by"] == "court" and n["document"] == "uznesenie" and n["court_code"] == "102"
    assert n["decision"] == "Súd začína konkurzné konanie voči dlžníkovi."
    assert n["header"].startswith("Okresný súd Bratislava I") and "attachments" not in n


@pytest.mark.anyio
async def test_long_texts_are_cut(api, monkeypatch):
    monkeypatch.setattr(tools, "TEXT_LIMIT_ONE", 40)
    n = (await tools.insolvency_get(notice_id=900149))["notice"]
    assert n["truncated"] is True and n["text"].endswith("[…]")


@pytest.mark.anyio
async def test_missing_and_bad_ids(api):
    with pytest.raises(ToolError, match="No proceeding 1"):
        await tools.insolvency_get(proceeding_id=1)
    with pytest.raises(ToolError, match="No notice 5"):
        await tools.insolvency_get(notice_id=5)
    with pytest.raises(ValueError, match="not number"):
        await tools.client().proceeding("abc")  # type: ignore[arg-type]
