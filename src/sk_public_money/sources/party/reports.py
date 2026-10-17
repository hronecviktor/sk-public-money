"""Reading a party's annual report (výročná správa) PDF: which pages carry a real text layer, their
text, and images of the pages that are scans, for the model to read itself.

Most reports are scanned paper. A page counts as text only when its text is real (a document exported
from Word or similar). Some scanners add their own OCR layer (invisible text under the page image);
those layers are badly garbled (IBANs like "SK87 ... 2L34195t0", dates like "15.!.2024"), so such
pages are treated as scans and labelled scanner_ocr.

The registers the law asks for (zákon č. 85/2005 Z. z., § 30 ods. 2) are lettered in most reports:
F loans and credits (pôžičky, úvery), G gifts and other free benefits (dary, iné bezodplatné plnenia),
J membership fees above twice the minimum wage (členské príspevky), K overdue liabilities with the
creditors (often suppliers with IČO). Parties lay them out each in their own way.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from ...text import fold

log = logging.getLogger(__name__)

MIN_LETTERS = 80
MAX_TEXT_CHARS = 15_000
THUMB_WIDTH, THUMB_HEIGHT = 300, 424
THUMB_COLUMNS, THUMB_ROWS = 4, 3
OCR_DPI = 300

# Words that name a register, matched on the folded text of text pages to point at the pages.
REGISTERS = {
    "loans (F)": re.compile(r"poziciek|pozick|\buver"),
    "gifts (G)": re.compile(r"\bdar(?:y|ov|och|ca|cu)?\b|bezodplatn"),
    "membership fees (J)": re.compile(r"clensk\w*\s+prispev"),
    "overdue liabilities (K)": re.compile(r"po\s+lehote\s+splatnosti"),
    "suppliers": re.compile(r"dodavatel"),
}
CONTENTS = re.compile(r"(?i)\bobsah\b")


def classify(path: Path) -> list[dict]:
    """[{page, layer}] for every page: text (a real text layer), scan (no text), or scanner_ocr (an
    invisible OCR layer under a scanned image, treated as a scan)."""
    reader = PdfReader(str(path))
    out = []
    for n, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001  (a broken page is counted as unread, not fatal)
            log.warning("%s page %s: %s", path.name, n, exc)
            text = ""
        if len(re.findall(r"[^\W\d_]", text)) < MIN_LETTERS:
            out.append({"page": n, "layer": "scan"})
            continue
        contents = page.get_contents()
        data = contents.get_data() if contents is not None else b""
        # Text render mode 3 is invisible text: an OCR layer under the page image.
        if re.search(rb"(?<![\d.])3\s+Tr\b", data):
            out.append({"page": n, "layer": "scanner_ocr"})
            continue
        head = " ".join(text.split())
        folded = fold(text)
        out.append({
            "page": n, "layer": "text",
            "mentions": [name for name, rx in REGISTERS.items() if rx.search(folded)],
            "contents": bool(CONTENTS.search(head[:300])),
        })
    return out


def ranges(numbers: list[int]) -> str:
    """[1, 2, 3, 7, 9, 10] -> '1-3, 7, 9-10'"""
    numbers = sorted(set(numbers))
    out, start = [], None
    for i, n in enumerate(numbers):
        start = n if start is None else start
        if i + 1 == len(numbers) or numbers[i + 1] != n + 1:
            out.append(f"{start}-{n}" if n != start else str(n))
            start = None
    return ", ".join(out)


def parse_pages(spec: str, total: int) -> list[int]:
    """'33-35, 40' -> [33, 34, 35, 40], checked against the page count."""
    pages: list[int] = []
    for part in re.split(r"[,;\s]+", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"Pages are numbers or ranges like '33-35, 40', got: {spec}")
        first, last = int(m[1]), int(m[2] or m[1])
        if first < 1 or last < first:
            raise ValueError(f"Bad page range: {part}")
        pages += range(first, last + 1)
    pages = list(dict.fromkeys(pages))
    beyond = [p for p in pages if p > total]
    if beyond:
        raise ValueError(f"The report has {total} pages; there is no page {beyond[0]}.")
    return pages


def coverage(pages: list[dict]) -> dict:
    """The summary kept in the index and shown to the model."""
    by = lambda layer: [p["page"] for p in pages if p["layer"] == layer]  # noqa: E731
    out = {
        "pages": len(pages),
        "text_pages": ranges(by("text")),
        "scanned_pages": ranges(by("scan") + by("scanner_ocr")),
    }
    if by("scanner_ocr"):
        out["scanner_ocr_pages"] = ranges(by("scanner_ocr"))
    contents = [p["page"] for p in pages if p.get("contents")]
    if contents:
        out["contents_pages"] = ranges(contents)
    mentions: dict[str, list[int]] = {}
    for p in pages:
        for name in p.get("mentions", []):
            mentions.setdefault(name, []).append(p["page"])
    if mentions:
        out["text_pages_mentioning"] = {k: ranges(v) for k, v in mentions.items()}
    return out


def page_text(path: Path, number: int) -> str:
    """A text page's text, with its columns kept apart (pypdf layout mode)."""
    page = PdfReader(str(path)).pages[number - 1]
    try:
        text = page.extract_text(extraction_mode="layout") or ""
    except Exception:  # noqa: BLE001  (layout mode fails on some pages; plain text still works)
        text = ""
    if len(text.strip()) < 20:
        text = page.extract_text() or ""
    lines = [line.rstrip() for line in text.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    return text[:MAX_TEXT_CHARS] + ("\n[… text cut]" if len(text) > MAX_TEXT_CHARS else "")


def _render(pdf: pdfium.PdfDocument, number: int, dpi: int) -> Image.Image:
    page = pdf[number - 1]
    try:
        return page.render(scale=dpi / 72, grayscale=True).to_pil().convert("L")
    finally:
        page.close()


def _jpeg(img: Image.Image, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def page_images(path: Path, numbers: list[int], dpi: int, rotate: int = 0) -> list[bytes]:
    """JPEG images of pages, grayscale, turned clockwise by rotate degrees (for tables scanned sideways)."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        return [_jpeg(_render(pdf, n, dpi).rotate(-rotate, expand=True)) for n in numbers]
    finally:
        pdf.close()


def page_count(path: Path) -> int:
    pdf = pdfium.PdfDocument(str(path))
    try:
        return len(pdf)
    finally:
        pdf.close()


def contact_sheets(path: Path, numbers: list[int]) -> list[bytes]:
    """Thumbnails of pages, 12 to a sheet with the page number on each, so the model can spot the
    register tables and pick pages to read in full."""
    per_sheet = THUMB_COLUMNS * THUMB_ROWS
    font = ImageFont.load_default(size=28)
    pdf = pdfium.PdfDocument(str(path))
    sheets = []
    try:
        for start in range(0, len(numbers), per_sheet):
            chunk = numbers[start : start + per_sheet]
            rows = -(-len(chunk) // THUMB_COLUMNS)
            sheet = Image.new("L", (THUMB_COLUMNS * (THUMB_WIDTH + 10) + 10, rows * (THUMB_HEIGHT + 44) + 10), 255)
            draw = ImageDraw.Draw(sheet)
            for i, n in enumerate(chunk):
                page = pdf[n - 1]
                w, h = page.get_size()
                scale = min(THUMB_WIDTH / w, THUMB_HEIGHT / h)
                thumb = page.render(scale=scale, grayscale=True).to_pil().convert("L")
                page.close()
                x = 10 + (i % THUMB_COLUMNS) * (THUMB_WIDTH + 10)
                y = 10 + (i // THUMB_COLUMNS) * (THUMB_HEIGHT + 44)
                draw.text((x, y), f"page {n}", fill=0, font=font)
                sheet.paste(thumb, (x, y + 34))
                draw.rectangle((x - 1, y + 33, x + thumb.width, y + 34 + thumb.height), outline=128)
            sheets.append(_jpeg(sheet, quality=70))
    finally:
        pdf.close()
    return sheets


@lru_cache(maxsize=1)
def tesseract() -> str | None:
    """The tesseract binary when it is installed with Slovak (slk), else None."""
    exe = shutil.which("tesseract")
    if not exe:
        return None
    try:
        langs = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=20).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if "slk" in langs else None


def ocr_pages(path: Path, numbers: list[int]) -> list[str]:
    exe = tesseract()
    if not exe:
        raise RuntimeError("tesseract with Slovak (slk) is not installed")
    pdf = pdfium.PdfDocument(str(path))
    out = []
    try:
        for n in numbers:
            buf = io.BytesIO()
            _render(pdf, n, OCR_DPI).save(buf, "PNG")
            done = subprocess.run([exe, "stdin", "stdout", "-l", "slk"], input=buf.getvalue(), capture_output=True, timeout=180)
            out.append(done.stdout.decode("utf-8", "replace").strip())
    finally:
        pdf.close()
    return out
