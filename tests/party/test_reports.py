"""Annual-report tests: the minv.sk listing pages and PDFs are mocked. The party, its people and the
PDFs are fictional and generated here; the page shapes are copied from real minv.sk pages."""

import io
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

from sk_public_money.sources.party import client as client_mod
from sk_public_money.sources.party import index as index_mod
from sk_public_money.sources.party import parse, reports, sync, tools

BASE = "https://www.minv.sk"
PDF_DIR = "/swift_data/source/statna_komisia_pre_volby/30_annual_report"

OVERVIEW = """<ul>
<li><a href="/?vyrocnespravyps2015" class="menu-item-link">Výročné správy politických strán a politických hnutí za rok 2015</a></li>
<li><a href="/?vyrocne-spravy-politickych-stran-a-politickych-hnuti-za-rok-2024" class="menu-item-link">Výročné správy politických strán a politických hnutí za rok 2024</a></li>
</ul><p><a title="x" href="http://www.minv.sk/?vyrocnespravyps2015">Výročné správy politických strán a politických hnutí za rok 2015</a></p>"""

YEAR_2024 = f"""<h1>Výročné správy politických strán a politických hnutí za rok 2024</h1><div>
<p><a title="Fiktívna strana" href="{PDF_DIR}/ar2024/VS24r_FS.pdf">Fiktívna strana (PDF, 1 MB)</a>*</p>
<p><a title="Vymyslené hnutie" href="{PDF_DIR}/ar2024/VS24r_VH.pdf">Vymyslené hnutie (PDF, 3 MB)</a></p>
<p><a title="Vymyslené hnutie" href="{PDF_DIR}/ar2024/VS24r_VH_f.pdf">Vymyslené hnutie (PDF, 3 MB)</a> (do 01.02.2025 s názvom: Staré hnutie)</p>
<p>aktualizácia: 19.02.2026</p>
<p>* k dátumu aktualizácie výročná správa obsahuje nedostatky.</p>
<p>** politické strany PRÍKLAD a UKÁŽKA nepredložili svoju výročnú správu za rok 2024.</p>
<p><a title="Metodické usmernenie" href="{PDF_DIR}/ar2024/oznam/sprievodny_vs24.pdf">Metodické usmernenie (PDF, 477 kB)</a></p>
</div>"""

YEAR_2015 = f"""<p><a href="{PDF_DIR}/ar2015/VS_12345678_I_2015_20160728.pdf" target="_blank">Fiktívna strana (PDF, 4 MB)</a>&nbsp;</p>
<p><a href="{PDF_DIR}/ar2015/sprievodny_list_2015.pdf">Sprievodný list (PDF, 1 MB)</a></p>"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- a generated report: text pages, a scanned page and a page with a scanner's invisible OCR layer ---

def _text_page_pdf(lines: list[str], *, invisible: bool = False) -> bytes:
    """A one-page PDF with real text (Helvetica), or with the same text invisible (render mode 3)."""
    ops = ["BT", "/F1 11 Tf", "3 Tr" if invisible else "0 Tr", "50 800 Td", "14 TL"]
    ops += [f"({line}) '" for line in lines]
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def _scan_page_pdf() -> bytes:
    img = Image.new("L", (595, 842), 255)
    draw = ImageDraw.Draw(img)
    draw.text((60, 60), "G. Evidencia darov (scan)", fill=0)
    draw.rectangle((50, 100, 545, 300), outline=0)
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=72)
    return buf.getvalue()


FILLER = "Politicka strana Fiktivna strana predklada vyrocnu spravu podla zakona o politickych stranach."
REPORT_PAGES = [
    _text_page_pdf(["OBSAH", "F. Evidencia prijmov z pozicky a uverov 2", "G. Evidencia darov 3", FILLER]),
    _text_page_pdf(["F. Evidencia prijmov z poziciek a uverov", "Strana v roku 2024 neprijala ziadnu pozicku.", FILLER]),
    _scan_page_pdf(),
    _text_page_pdf(["G. Evidencia darov", "1 Jan Vymysleny  Hlavna 1, Nitra  500,00 EUR  4.3.2024", FILLER], invisible=True),
]


def report_pdf() -> bytes:
    writer = PdfWriter()
    for page in REPORT_PAGES:
        writer.append(PdfReader(io.BytesIO(page)))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# --- listing pages ---

def test_report_links_prefers_corrected_and_skips_guidance():
    rows = parse.report_links(YEAR_2024, 2024)
    assert [(r["party"], r["file"], r["corrected"], r["deficient"]) for r in rows] == [
        ("Fiktívna strana", "VS24r_FS.pdf", False, True),
        ("Vymyslené hnutie", "VS24r_VH_f.pdf", True, False),
    ]
    assert rows[1]["listing_note"] == "(do 01.02.2025 s názvom: Staré hnutie)"
    assert rows[0]["url"] == f"{BASE}{PDF_DIR}/ar2024/VS24r_FS.pdf"
    assert [r["file"] for r in parse.report_links(YEAR_2015, 2015)] == ["VS_12345678_I_2015_20160728.pdf"]


def test_year_pages_and_notes():
    assert parse.year_pages(OVERVIEW) == {
        2015: f"{BASE}/?vyrocnespravyps2015",
        2024: f"{BASE}/?vyrocne-spravy-politickych-stran-a-politickych-hnuti-za-rok-2024",
    }
    assert parse.year_notes(YEAR_2024) == {
        "page_updated": "2026-02-19",
        "not_filed": "politické strany PRÍKLAD a UKÁŽKA nepredložili svoju výročnú správu za rok 2024.",
    }


def test_index_keeps_coverage_while_the_pdf_address_is_unchanged(tmp_path):
    idx = index_mod.ReportIndex(tmp_path / "r.sqlite")
    rows = parse.report_links(YEAR_2024, 2024)
    idx.replace_year(2024, "u", rows, {})
    idx.set_coverage("2024/VS24r_FS.pdf", 4, {"pages": 4})
    assert idx.replace_year(2024, "u", rows, {}) == {"added": 0, "kept": 2, "dropped": 0}
    assert idx.get("2024/VS24r_FS.pdf")["coverage"] == {"pages": 4}
    moved = [{**rows[0], "url": rows[0]["url"] + "?v2"}]
    assert idx.replace_year(2024, "u", moved, {}) == {"added": 0, "kept": 1, "dropped": 1}
    assert "coverage" not in idx.get("2024/VS24r_FS.pdf")
    assert [r["party"] for r in idx.find(party="vymyslene")] == []
    assert [r["party"] for r in idx.find(party="FIKTIVNA")] == ["Fiktívna strana"]
    assert [r["party"] for r in idx.find(party="fs", year=2024)] == ["Fiktívna strana"]  # the file's short name


# --- reading a PDF ---

def test_classify_and_coverage(tmp_path):
    path = tmp_path / "r.pdf"
    path.write_bytes(report_pdf())
    pages = reports.classify(path)
    assert [p["layer"] for p in pages] == ["text", "text", "scan", "scanner_ocr"]
    cov = reports.coverage(pages)
    assert cov["pages"] == 4 and cov["text_pages"] == "1-2" and cov["scanned_pages"] == "3-4"
    assert cov["scanner_ocr_pages"] == "4" and cov["contents_pages"] == "1"
    assert cov["text_pages_mentioning"]["loans (F)"] == "1-2"
    assert reports.ranges([1, 2, 3, 7, 9, 10]) == "1-3, 7, 9-10"


def test_parse_pages():
    assert reports.parse_pages("3-5, 8", 10) == [3, 4, 5, 8]
    with pytest.raises(ValueError, match="no page 11"):
        reports.parse_pages("11", 10)
    with pytest.raises(ValueError, match="ranges"):
        reports.parse_pages("G", 10)


# --- the tools ---

@pytest.fixture
def minv(monkeypatch, tmp_path):
    calls: list[httpx.Request] = []
    pdf = report_pdf()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        url = str(request.url)
        if url == f"{BASE}/?vyrocnespravyps":
            return httpx.Response(200, text=OVERVIEW)
        if url == f"{BASE}/?vyrocnespravyps2015":
            return httpx.Response(200, text=YEAR_2015)
        if url == f"{BASE}/?vyrocne-spravy-politickych-stran-a-politickych-hnuti-za-rok-2024":
            return httpx.Response(200, text=YEAR_2024)
        if url.endswith(".pdf"):
            return httpx.Response(200, content=pdf)
        return httpx.Response(404, text="not found")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tools, "_minv", client_mod.MinvClient(http=http, cache=tmp_path, interval=0))
    monkeypatch.setattr(tools, "_reports", index_mod.ReportIndex(tmp_path / "reports.sqlite"))
    monkeypatch.setattr(tools.localdb, "ensure_free_space", lambda *a, **k: None)
    return calls


@pytest.mark.anyio
async def test_annual_reports_builds_the_listing_inline(minv):
    out = await tools.party_annual_reports(party="vymyslene hnutie")
    assert "fetched from minv.sk" in out["listing"]
    assert out["reports"] == [{
        "year": 2024, "party": "Vymyslené hnutie", "url": f"{BASE}{PDF_DIR}/ar2024/VS24r_VH_f.pdf",
        "corrected_version": True, "listing_note": "(do 01.02.2025 s názvom: Staré hnutie)",
        "coverage": "not read yet: party_read_report reads it",
    }]
    assert out["year_notes"][2024]["not_filed"].startswith("politické strany PRÍKLAD")
    years = await tools.party_annual_reports()
    assert years["years"][2015]["reports"] == 1 and years["years"][2024]["reports"] == 2
    assert len(minv) == 3  # overview + two year pages, once


@pytest.mark.anyio
async def test_read_report_thumbnails_then_pages(minv):
    blocks = await tools.party_read_report(party="fiktivna strana", year=2024)
    head = json.loads(blocks[0].text)
    assert head["coverage"]["text_pages"] == "1-2" and head["thumbnails"] == "pages 1-4 of 4"
    assert [b.type for b in blocks[1:]] == ["image"]

    blocks = await tools.party_read_report(party="fiktivna strana", year=2024, pages="2-4", rotate=90)
    head = json.loads(blocks[0].text)
    assert [(p["page"], p["returned_as"]) for p in head["pages"]] == [(2, "text"), (3, "image"), (4, "image")]
    assert "Check every name" in head["note"]
    assert blocks[1].type == "text" and "neprijala ziadnu" in blocks[1].text
    assert "garbled" in blocks[4].text and blocks[5].type == "image"
    # The coverage is stored, and the PDF was downloaded once.
    listed = await tools.party_annual_reports(party="fiktivna", year=2024)
    assert listed["reports"][0]["coverage"]["scanned_pages"] == "3-4"
    assert "layers" not in listed["reports"][0]["coverage"]
    assert len([c for c in minv if c.url.path.endswith(".pdf")]) == 1


@pytest.mark.anyio
async def test_read_report_limits(minv):
    with pytest.raises(ToolError, match="At most 3 pages"):
        await tools.party_read_report(party="fiktivna", year=2024, pages="1-4", dpi=200)
    with pytest.raises(ToolError, match="No 2023 annual report"):
        await tools.party_read_report(party="fiktivna", year=2023)


@pytest.mark.anyio
async def test_ocr_falls_back_to_images_without_tesseract(minv, monkeypatch):
    monkeypatch.setattr(reports, "tesseract", lambda: None)
    blocks = await tools.party_read_report(party="fiktivna", year=2024, pages="3", ocr=True)
    head = json.loads(blocks[0].text)
    assert "not installed" in head["ocr"] and blocks[-1].type == "image"


@pytest.mark.anyio
async def test_sync_skips_a_year_without_a_page(tmp_path, minv):
    idx = index_mod.ReportIndex(tmp_path / "s.sqlite")
    out = await sync.refresh(idx, tools.minv(), years=[2024, 2025])
    assert list(out) == [2024]
