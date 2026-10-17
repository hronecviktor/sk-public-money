"""Obchodný vestník tests: parsing of the portal's XML forms, a synthetic Datahub dump streamed
from a mock server, the sync API top-up and the tool. Companies and people are fictional; the XML
and dump shapes are copied from real Datahub data."""

import gzip
import json
from xml.sax.saxutils import escape

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sk_public_money.sources.ov import index as index_mod
from sk_public_money.sources.ov import parse, sync, tools


@pytest.fixture
def anyio_backend():
    return "asyncio"


NS = 'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="/PortalJustice/XmlFormularObchodnyVestnik/"'

LIQUIDATION = f"""\ufeff<?xml version="1.0" encoding="utf-8"?>
<OznamenieVyzvaLikvidatorov2020 {NS}>
  <Likvidatori><Likvidator><OsobaTyp xmlns="" Kod="Fyzická osoba">FO</OsobaTyp><OsobaFO xmlns="">
    <Meno><TitulPred>Mgr.</TitulPred><TitulZa /><Meno>Jana</Meno><Priezvisko>Vymyslená</Priezvisko></Meno>
    <Bydlisko><Ulica>Skúšobná</Ulica><Cislo>1</Cislo><Obec>Nitra</Obec><Psc>94901</Psc><Stat>Slovenská republika</Stat></Bydlisko>
    <DatumNarodenia>1980-01-02T00:00:00</DatumNarodenia></OsobaFO></Likvidator></Likvidatori>
  <TypZverejnenia Kod="OznamenieOVstupeDoLikvidacieAVyzvaNaPrihlaseniePohladavok">Oznámenie o vstupe do likvidácie a výzva na prihlásenie pohľadávok</TypZverejnenia>
  <Podnik><PravnaForma Kod="AG">obchodná spoločnosť</PravnaForma><UdajePodnik>
    <ObchodneMenoNazov xmlns="">Fiktívna Firma s.r.o. v likvidácii</ObchodneMenoNazov></UdajePodnik>
    <Ico>01 234 567</Ico></Podnik>
  <NaZakladeRozhodnutia><RozhodnutieDatum xmlns="">2026-09-02</RozhodnutieDatum><RozhodnutieDatumZaciatkuLikvidacie xmlns="">0001-01-01T00:00:00</RozhodnutieDatumZaciatkuLikvidacie></NaZakladeRozhodnutia>
</OznamenieVyzvaLikvidatorov2020>"""

MERGER_TEXT = ("<p>spoločnosti <strong>Fiktívna Firma s.r.o.</strong>, IČO: 01 234 567, so spoločnosťou "
               "<strong>Vzorová Zanikajúca a.s.</strong>, IČO: 76 543 210, pričom nástupníckou spoločnosťou je "
               "Fiktívna Firma s.r.o.</p>")
MERGER = f"""<?xml version="1.0" encoding="utf-8"?>
<ZlucenieSplynutieProjektuZbierkyListin {NS}>
  <Spolocnost><ObchodneMenoNazov xmlns="">Fiktívna Firma s.r.o.</ObchodneMenoNazov><Ico xmlns="">1234567</Ico></Spolocnost>
  <Datum>2019-12-29T00:00:00</Datum><NaZaklade Kod="ZLUC">zlúčenie</NaZaklade>
  <ZverejnujeText>{escape(MERGER_TEXT)}</ZverejnujeText>
</ZlucenieSplynutieProjektuZbierkyListin>"""

KONKURZ = """<?xml version="1.0" encoding="utf-8"?>
<KonkurzARestrukt xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Autor><Sud>Okresný súd Nitra</Sud><SpisovaZnacka>3K/1/2020</SpisovaZnacka></Autor>
  <Dlznik><Nazov>Fiktívna Firma s.r.o.</Nazov><ICO>01234567</ICO></Dlznik>
  <Spravcovia><Spravca><Nazov>Správca Príklad k.s.</Nazov><ICO>11111111</ICO></Spravca></Spravcovia>
  <Text><Druh>Uznesenie</Druh><Rozhodnutie>Súd vyhlasuje konkurz na majetok dlžníka.</Rozhodnutie></Text>
</KonkurzARestrukt>"""

TRUSTEE_FOR_PERSON = f"""<?xml version="1.0" encoding="utf-8"?>
<OznameniaSpravcov {NS}><Upadca><ObchodneMenoNazov>Ján Ukážkový</ObchodneMenoNazov><Ico>09.12.1981</Ico></Upadca>
<DruhPodania Kod="16">Iné zverejnenie</DruhPodania></OznameniaSpravcov>"""

OR_OLD = """<?xml version="1.0" encoding="utf-8"?>
<ORPodanie xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <ORZnacka>R1</ORZnacka><SudKod>B</SudKod><TypKod>vy</TypKod><TypNazov>Výmazy</TypNazov>
  <TextPodania>ODDIEL: Sro
OBCHODNÉ MENO: Fiktívna Firma s.r.o.
IČO: 01 234 567
DEŇ VÝMAZU: 29.12.2016</TextPodania>
</ORPodanie>"""


# --- parsing -----------------------------------------------------------------------------------

def test_kinds_and_categories():
    assert parse.kind_of("L006019-OV_LIKVIDATOR_2020-v-1.xml") == "OV_LIKVIDATOR_2020"
    assert parse.kind_of("O000320-OV_PREROZDELENIE_KAPITALOVEHO_FONDU-v-2-c97f1fcf@x.pdf") == "OV_PREROZDELENIE_KAPITALOVEHO_FONDU"
    assert parse.category_of("OV_PREROZDELENIE_KAPITALOVEHO_FONDU") == "capital"
    assert parse.category_of("OV_OZNAMENIE_ULO_NAVR_ZML_ZLU_SPLY_PROJ_ZL") == "dissolution"
    assert parse.category_of("OV_OR_PODANIE_XML") == "register"
    assert parse.category_of("OV_B_OZNAMENIA_SPRAVCOV") == "insolvency"
    assert parse.category_of("OV_ZMLUVA") is None
    assert parse.category_of("OV_STATNA_POMOC_A_INE_PROGRAMY_PODPORY") is None


def test_ico_normalisation():
    assert parse.normalize_ico("31 393 781") == "31393781"
    assert parse.normalize_ico("205788") == "00205788"
    assert parse.normalize_ico("09.12.1981") is None
    assert parse.normalize_ico("000000") is None


def test_liquidation_keeps_company_not_liquidator():
    r = parse.parse(10, "L1-OV_LIKVIDATOR_2020-v-1.xml", LIQUIDATION, "2026-09-30", "190/2026")
    assert r["category"] == "liquidation" and r["issue"] == "190/2026"
    assert r["title"] == "Oznámenie o vstupe do likvidácie a výzva na prihlásenie pohľadávok"
    assert r["companies"] == [{"ico": "01234567", "name": "Fiktívna Firma s.r.o. v likvidácii"}]
    # The liquidator is published as is, birth date included; the forms' empty date is dropped.
    assert "Priezvisko: Vymyslená" in r["details"] and "DatumNarodenia: 1980-01-02;" in r["details"]
    assert "0001-01-01" not in r["details"]


def test_merger_indexes_the_other_company_as_mentioned():
    r = parse.parse(11, "O2-OV_OZNAMENIE_ULO_NAVR_ZML_ZLU_SPLY_PROJ_ZL-v-1.xml", MERGER, "2020-01-02", "1/2020")
    assert r["category"] == "dissolution" and r["title"] == "zlúčenie"
    assert [c["ico"] for c in r["companies"]] == ["01234567"]
    assert r["mentioned_icos"] == ["76543210"]
    assert r["text"].startswith("spoločnosti Fiktívna Firma s.r.o., IČO: 01 234 567")


def test_konkurz_skips_trustee_and_later_notices():
    r = parse.parse(12, "K1-OV_KONKURZY_A_RESTRUKT-v-1.xml", KONKURZ, "2020-03-02", "40/2020")
    assert [c["ico"] for c in r["companies"]] == ["01234567"] and r["title"] == "Uznesenie"
    assert parse.parse(13, "K1-OV_KONKURZY_A_RESTRUKT-v-1.xml", KONKURZ, "2025-10-01", "1/2025") is None
    # A person's birth date typed into the IČO field is not an IČO.
    assert parse.parse(14, "K2-OV_B_OZNAMENIA_SPRAVCOV-v-1.xml", TRUSTEE_FOR_PERSON, "2020-03-02", "40/2020") is None


def test_old_register_filing_takes_ico_from_text():
    r = parse.parse(15, "R1-OV_OR_PODANIE-v-1.xml", OR_OLD, "2017-01-02", "1/2017")
    assert r["category"] == "register" and r["title"] == "Výmazy"
    assert r["companies"] == [{"ico": "01234567", "name": "Fiktívna Firma s.r.o."}]


# --- dump and sync -----------------------------------------------------------------------------

def copy_field(value):
    if value is None:
        return "\\N"
    return value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def make_dump() -> bytes:
    raw = [
        (10, 1, "L1-OV_LIKVIDATOR_2020-v-1.xml", LIQUIDATION, "2026-09-30 03:00:00.5"),
        (11, 2, "O2-OV_OZNAMENIE_ULO_NAVR_ZML_ZLU_SPLY_PROJ_ZL-v-1.xml", MERGER, "2020-01-02 03:00:00.1"),
        (12, 2, "ZML1-OV_ZMLUVA-v-1-abc@zmluva.pdf", None, "2020-01-02 03:00:00.2"),
        (13, 2, "R1-OV_OR_PODANIE-v-1.xml", OR_OLD, "2026-10-10 03:02:27.235828"),
    ]
    lines = [
        "--", "-- PostgreSQL database dump", "--",
        "COPY ov.bulletin_issues (id, year, number, published_at, created_at, updated_at) FROM stdin;",
        "1\t2026\t190\t2026-09-30 00:00:00\t2026-09-30 03:00:00\t2026-09-30 03:00:00",
        "2\t2020\t1\t2020-01-02 00:00:00\t2020-01-02 03:00:00\t2020-01-02 03:00:00",
        "\\.", "",
        "COPY ov.konkurz_vyrovnanie_issues (id, announcement) FROM stdin;",
        "1\tignored",
        "\\.", "",
        "COPY ov.raw_issues (id, bulletin_issue_id, file_name, content, created_at, updated_at) FROM stdin;",
        *["\t".join([str(i), str(b), name, copy_field(content), upd, upd]) for i, b, name, content, upd in raw],
        "\\.", "",
    ]
    data = "\n".join(lines).encode()
    # Two gzip members, split mid-row, as a concatenated .gz may be.
    return gzip.compress(data[:700]) + gzip.compress(data[700:])


def test_copy_unescape():
    assert sync.unescape("\\N") is None
    assert sync.unescape("a\\tb\\nc\\\\d") == "a\tb\nc\\d"


def test_build_from_streamed_dump(tmp_path):
    dump = make_dump()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=dump, headers={"Content-Length": str(len(dump))})

    idx = index_mod.OVIndex(tmp_path / "ov.sqlite")
    stats = sync.build_from_dump(idx, "https://dump.test/ov.sql.gz", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert stats["raw_rows"] == 4 and stats["kept"] == 3
    assert idx.get_meta("cursor_since") == "2026-10-10T03:02:27.235828Z" and idx.get_meta("cursor_last_id") == "13"
    rows, total = idx.search("01234567")
    assert total == 3 and [r["id"] for r in rows] == [10, 13, 11]  # newest first
    assert rows[0]["issue"] == "190/2026" and rows[0]["date"] == "2026-09-30"
    rows, total = idx.search("76543210")
    assert total == 1 and rows[0]["mentioned_only"] is True
    assert idx.search("01234567", category="register")[1] == 1
    assert idx.search("01234567", since="2021-01-01")[1] == 1
    assert index_mod.exists(tmp_path / "ov.sqlite")


@pytest.fixture
def built(tmp_path, monkeypatch):
    dump = make_dump()
    idx = index_mod.OVIndex(tmp_path / "ov.sqlite")
    sync.build_from_dump(idx, "https://dump.test/ov.sql.gz",
                         http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=dump))))
    return idx


def api_row(i, file_name, content, published="2026-10-14T00:00:00.000000Z"):
    return {"id": i, "file_name": file_name, "content": content, "created_at": "x", "updated_at": f"2026-10-14T03:00:0{i % 10}.0Z",
            "bulletin_issue": {"id": 9, "year": 2026, "number": 199, "published_at": published}}


@pytest.mark.anyio
async def test_top_up_follows_cursor_and_replaces(built):
    seen = []
    pages = [
        ([api_row(20, "O3-OV_OZNAMENIE_ZNIZENIE_ZAKLADNEHO_IMANIA_SRO-v-1.xml", LIQUIDATION.replace("OV_LIKVIDATOR", "x"))], True),
        ([api_row(10, "L1-OV_LIKVIDATOR_2020-v-1.xml", None)], False),  # the liquidation notice lost its content
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        rows, more = pages[len(seen) - 1]
        link = {"Link": "<https://datahub.test/next>; rel='next'"} if more else {}
        return httpx.Response(200, json=rows, headers=link)

    client = sync.DatahubClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), interval=0)
    result = await sync.top_up(built, client)
    assert result == {"pages": 2, "kept": 1, "caught_up": True}
    assert seen[0] == {"since": "2026-10-10T03:02:27.235828Z", "last_id": "13"}
    assert seen[1] == {"since": "2026-10-14T03:00:00.0Z", "last_id": "20"}
    rows, _ = built.search("01234567")
    assert [r["id"] for r in rows] == [20, 13, 11]
    assert rows[0]["category"] == "capital" and rows[0]["issue"] == "199/2026"


# --- tool --------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_tool_without_index(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_index", None)
    monkeypatch.setattr(index_mod, "default_path", lambda: tmp_path / "none.sqlite")
    with pytest.raises(ToolError, match="sk-public-money-sync ov"):
        await tools.ov_search_notices(ico="01234567")


@pytest.mark.anyio
async def test_tool_search(built, monkeypatch):
    monkeypatch.setattr(tools, "_index", built)
    result = await tools.ov_search_notices(ico="1 234 567", kind="dissolution")
    assert result["ico"] == "01234567" and result["total"] == 1
    n = result["notices"][0]
    assert n["title"] == "zlúčenie" and n["mentioned_icos"] == ["76543210"]
    assert n["source"].endswith("/raw_issues/11")
    assert "informatívny charakter" in result["notes"][0]
    assert result["index_covers"] == {"from": "2020-01-02", "to": "2026-09-30"}
    with pytest.raises(ToolError, match="8 digits"):
        await tools.ov_search_notices(ico="12.3.1980")
    other = await tools.ov_search_notices(ico="76543210")
    assert other["notices"][0]["mentioned_only"] is True and any("mentioned_only" in n for n in other["notes"])
