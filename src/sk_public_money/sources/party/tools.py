"""Tools for political-party financing: transparent accounts (Slovenská sporiteľňa) and the annual
reports parties file with the State Commission (minv.sk), read on demand: text pages as text, scanned
pages as images for the model to read."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import anyio
from mcp.types import ImageContent, TextContent
from pydantic import Field

from ... import localdb
from ...app import READ_ONLY, mcp, user_errors
from ...http import SourceUnavailable, cache_dir
from ...text import fold
from . import parse, reports, sync
from .client import MAX_PAGE, SLSP_WEB_URL, MinvClient, SLSPClient
from .index import ReportIndex

IBAN = re.compile(r"SK\d{22}")
ACCOUNT_NOTE = (
    "Slovenská sporiteľňa transparent accounts only; the list also holds candidates', campaigns' and "
    "charities' accounts. Parties' accounts at ČSOB, VÚB, Tatra banka or Fio are not covered."
)
COUNTERPARTY_NOTE = (
    "Counterparties are named as the bank shows them, with no IBAN or IČO: a matching name is not proof "
    "of the same person or company."
)

_slsp: SLSPClient | None = None


def slsp() -> SLSPClient:
    global _slsp
    if _slsp is None:
        _slsp = SLSPClient()
    return _slsp


def _day(value: str | None, name: str) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise ValueError(f"{name} must be a date YYYY-MM-DD, got: {value}") from None


def match_accounts(accounts: list[dict], query: str) -> list[dict]:
    """Accounts whose name contains every word of query, ignoring case and diacritics; exact (folded)
    names first, then the most recently updated."""
    words = fold(query).split()
    if not words:
        return []
    hits = [a for a in accounts if all(w in fold(a.get("name") or "") for w in words)]
    hits.sort(key=lambda a: a.get("updated") or "", reverse=True)
    hits.sort(key=lambda a: " ".join(fold(a.get("name") or "").split()) != " ".join(words))
    return hits


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def party_account_transactions(
    iban: Annotated[str | None, Field(description="IBAN of a Slovenská sporiteľňa transparent account (SK..., spaces allowed).")] = None,
    account: Annotated[str | None, Field(description="Words of the account name instead of iban, any case and diacritics (e.g. 'smer volby 2026'). Several matches are listed for you to pick an IBAN.")] = None,
    counterparty: Annotated[str | None, Field(description="Part of the counterparty name (payer or payee), any case and diacritics.")] = None,
    date_from: Annotated[str | None, Field(description="First day, YYYY-MM-DD.")] = None,
    date_to: Annotated[str | None, Field(description="Last day, YYYY-MM-DD.")] = None,
    min_amount: Annotated[float | None, Field(ge=0, description="Only movements of at least this many euros (in either direction unless direction is set).")] = None,
    direction: Annotated[Literal["any", "in", "out"], Field(description="in = received (donations, transfers in), out = paid.")] = "any",
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    page: Annotated[int, Field(ge=0, description="0-based page of results.")] = 0,
) -> dict:
    """Movements on a transparent account (transparentný účet) at Slovenská sporiteľňa, newest first:
    date, signed amount in EUR (negative = paid out), counterparty name, note, variable symbol. Parties
    and candidates must run campaigns through transparent accounts. Without iban or account, nothing is
    searched: give one."""
    if (iban is None) == (account is None):
        raise ValueError("Give exactly one of iban or account.")
    accounts = await slsp().accounts()
    if iban is not None:
        iban = iban.replace(" ", "").upper()
        if not IBAN.fullmatch(iban):
            raise ValueError(f"A Slovak IBAN is SK followed by 22 digits, got: {iban}")
        found = [a for a in accounts if a.get("iban") == iban]
        if not found:
            raise LookupError(f"{iban} is not a published Slovenská sporiteľňa transparent account. {ACCOUNT_NOTE}")
    else:
        found = match_accounts(accounts, account)
        if not found:
            raise LookupError(f"No Slovenská sporiteľňa transparent account is named like '{account}'. {ACCOUNT_NOTE}")
        if len(found) > 1:
            return {
                "accounts": [parse.account(a) for a in found[:50]],
                "matching_accounts": len(found),
                "note": "Several accounts match; call again with the iban of one. " + ACCOUNT_NOTE,
            }
    acct = found[0]
    query = {
        "counterparty": (counterparty or "").strip() or None,
        "date_from": _day(date_from, "date_from"),
        "date_to": _day(date_to, "date_to"),
    }
    least = max(min_amount or 0.0, 0.01)
    offset = page * limit
    if offset + limit > MAX_PAGE * 20:
        raise ValueError("That page is too deep; narrow the search by date or counterparty.")
    if direction == "any" and min_amount:
        # The API filters by signed amount, so large movements both ways take two queries, merged here.
        size = offset + limit
        if size > MAX_PAGE:
            raise ValueError(f"With min_amount in both directions, at most {MAX_PAGE} results can be paged; set direction or narrow by date.")
        got_in = await slsp().turnovers(acct["iban"], size=size, amount_from=least, **query)
        got_out = await slsp().turnovers(acct["iban"], size=size, amount_to=-least, **query)
        rows = got_in.get("turnovers", []) + got_out.get("turnovers", [])
        rows.sort(key=lambda t: (t.get("date") or "", int(t.get("turnoverId") or 0)), reverse=True)
        rows = rows[offset : offset + limit]
        total = (got_in.get("page") or {}).get("totalElements", 0) + (got_out.get("page") or {}).get("totalElements", 0)
    else:
        bounds = {}
        if direction == "in":
            bounds["amount_from"] = least
        elif direction == "out":
            bounds["amount_to"] = -least
        got = await slsp().turnovers(acct["iban"], page=page, size=limit, **query, **bounds)
        rows = got.get("turnovers", [])
        total = (got.get("page") or {}).get("totalElements", 0)
    out = {
        "account": parse.account(acct),
        "matching": total,
        "transactions": [parse.turnover(t) for t in rows],
        "has_more": offset + len(rows) < total,
        "source": f"Slovenská sporiteľňa transparent accounts ({SLSP_WEB_URL})",
        "note": COUNTERPARTY_NOTE,
    }
    if query["counterparty"]:
        out["matched_by"] = "name"
    return out


# --- annual reports (výročné správy) ------------------------------------------------------------

READ_NOTE = (
    "Registers in most reports: F loans (pôžičky, úvery), G gifts and other free benefits (dary, bezodplatné "
    "plnenia), J membership fees above twice the minimum wage (členské príspevky), K overdue liabilities. "
    "Read them with party_read_report; most reports are scans, which it returns as page images."
)
IMAGE_NOTE = (
    "Pages without a text layer are returned as images for you to read. Check every name, amount, date and "
    "IBAN you take from an image against the image, cite the page, and say when a value is hard to read."
)
OCR_LABEL = "OCR (tesseract), not the published text: check names and amounts against the page image."
MAX_PAGES = 6
MAX_PAGES_HIGH_DPI = 3
THUMBS_PER_CALL = 24
PDF_KEEP = 3600  # seconds a downloaded report stays in the cache
LAYER_CODES = {"text": "T", "scan": "S", "scanner_ocr": "O"}

_minv: MinvClient | None = None
_reports: ReportIndex | None = None
_reading = asyncio.Lock()


def minv() -> MinvClient:
    global _minv
    if _minv is None:
        _minv = MinvClient()
    return _minv


def report_index() -> ReportIndex:
    global _reports
    if _reports is None:
        _reports = ReportIndex()
    return _reports


async def _listing() -> dict | None:
    """Make sure the report listing exists and is reasonably fresh; returns a note when it had to be
    built or could not be refreshed."""
    idx = report_index()
    oldest = idx.oldest_listing()
    if oldest is None:
        try:
            await sync.refresh(idx, minv())
        except SourceUnavailable as exc:
            raise SourceUnavailable(f"The report listing has not been built and minv.sk could not be reached: {exc}") from exc
        return {"listing": "The report listing was fetched from minv.sk just now (about a dozen pages)."}
    if time.time() - oldest > sync.REFRESH_EVERY:
        newest = sorted(idx.years())[-sync.INLINE_YEARS :]
        try:
            await sync.refresh(idx, minv(), years=newest + [max(newest) + 1])
        except (SourceUnavailable, LookupError) as exc:
            return {"listing": f"Could not refresh the report listing ({exc}); run sk-public-money-sync party."}
    return None


def _public(rep: dict) -> dict:
    out = {k: v for k, v in rep.items() if k != "id"}
    if "coverage" in out:
        out["coverage"] = {k: v for k, v in out["coverage"].items() if k != "layers"}
    else:
        out["coverage"] = "not read yet: party_read_report reads it"
    return out


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def party_annual_reports(
    party: Annotated[str | None, Field(description="Words of the party's name or its short name (e.g. 'smer', 'progresivne slovensko', 'SaS'), any case and diacritics.")] = None,
    year: Annotated[int | None, Field(ge=2015, le=2100, description="The year the report covers (filed by 31 March of the next year).")] = None,
) -> dict:
    """Annual reports (výročné správy) of political parties filed with the State Commission for
    Elections, from minv.sk since 2015: party, year, PDF link, whether the file is a corrected version or
    was marked as deficient, and, once read, its page coverage (which pages have text and which are
    scans) and the text pages mentioning each register. The reports hold the registers of loans, gifts
    and membership fees: read them with party_read_report. Without party or year: the years listed."""
    note = await _listing()
    idx = report_index()
    years = idx.years()
    if party is None and year is None:
        out = {"years": {y: {"reports": v["reports"], **v["notes"]} for y, v in years.items()}, "note": "Give a party or a year to list reports."}
    else:
        found = idx.find(party=party, year=year)
        if not found:
            hint = f" for {year}" if year else ""
            raise LookupError(f"No annual report{hint} of a party named like '{party}'." if party else f"No annual reports listed for {year}.")
        out = {"reports": [_public(r) for r in found[:100]], "matching": len(found)}
        notes = {y: years[y]["notes"] for y in sorted({r["year"] for r in found}) if years.get(y, {}).get("notes")}
        if notes:
            out["year_notes"] = notes
        out["note"] = READ_NOTE
    out["source"] = "Štátna komisia pre voľby a kontrolu financovania politických strán, minv.sk (no licence published)"
    if note:
        out.update(note)
    return out


def _pick(found: list[dict], party: str) -> dict:
    if len(found) == 1:
        return found[0]
    wanted = " ".join(fold(party).split())
    exact = [r for r in found if " ".join(fold(r["party"]).split()) == wanted]
    if len(exact) == 1:
        return exact[0]
    names = "; ".join(sorted({r["party"] for r in found}))
    raise LookupError(f"Several reports match '{party}': {names}. Give more of the name.")


def _pdf_path(rep: dict) -> Path:
    return cache_dir() / "party" / "pdf" / rep["id"]


def _prune(keep: Path) -> None:
    """Delete downloaded reports older than PDF_KEEP: they are read on demand, not archived."""
    folder = cache_dir() / "party" / "pdf"
    if not folder.exists():
        return
    now = time.time()
    for f in folder.glob("*/*"):
        if f != keep and f.is_file() and now - f.stat().st_mtime > PDF_KEEP:
            f.unlink(missing_ok=True)


async def _open_report(rep: dict) -> tuple[Path, dict]:
    """The report's PDF on disk (downloaded if needed) and its coverage (read and stored if new)."""
    path = _pdf_path(rep)
    _prune(path)
    if not path.exists():
        localdb.ensure_free_space(path, 100 << 20, reserve=1 << 30)
        await minv().download_pdf(rep["url"], path)
    cov = rep.get("coverage")
    if not cov or "layers" not in cov:
        pages = await anyio.to_thread.run_sync(reports.classify, path)
        cov = reports.coverage(pages)
        cov["layers"] = "".join(LAYER_CODES[p["layer"]] for p in pages)
        report_index().set_coverage(rep["id"], len(pages), cov)
    return path, cov


def _text(value) -> TextContent:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=1)
    return TextContent(type="text", text=value)


def _image(data: bytes) -> ImageContent:
    return ImageContent(type="image", data=base64.b64encode(data).decode(), mime_type="image/jpeg")


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def party_read_report(
    party: Annotated[str, Field(description="Words of the party's name or its short name, as in party_annual_reports.")],
    year: Annotated[int, Field(ge=2015, le=2100, description="The year the report covers.")],
    pages: Annotated[str | None, Field(description=f"Pages to read, e.g. '33-35' or '5, 12' (at most {MAX_PAGES}). Without pages: the coverage and thumbnails of the pages, to find the registers.")] = None,
    thumbnails_from: Annotated[int, Field(ge=1, description=f"Without pages: the first page of the {THUMBS_PER_CALL} thumbnails returned.")] = 1,
    dpi: Annotated[int, Field(ge=80, le=200, description=f"Resolution of page images; above 150 at most {MAX_PAGES_HIGH_DPI} pages. Raise it for small print.")] = 130,
    rotate: Annotated[Literal[0, 90, 180, 270], Field(description="Turn page images clockwise by this many degrees, for tables scanned sideways.")] = 0,
    images: Annotated[bool, Field(description="Return text pages as images too (tables can be easier to read as a picture).")] = False,
    ocr: Annotated[bool, Field(description="Run tesseract (if installed with Slovak) on scanned pages and return its text instead of images.")] = False,
) -> list[TextContent | ImageContent]:
    """Read pages of a party's annual report. Pages with a real text layer come back as text; scanned
    pages come back as images (grayscale JPEG) for you to read. Without pages: the page count, which
    pages are text or scans, the text pages that mention each register, and thumbnail sheets (12 pages
    each, labelled with page numbers) to spot the register tables; then call again with pages. Gifts
    are usually section G, loans F, membership fees J. The PDF is downloaded from minv.sk on demand
    (1–35 MB, a few seconds)."""
    notes = await _listing()
    found = report_index().find(party=party, year=year)
    if not found:
        raise LookupError(f"No {year} annual report of a party named like '{party}'; see party_annual_reports.")
    rep = _pick(found, party)
    async with _reading:  # one PDF at a time
        path, cov = await _open_report(rep)
        layers = cov["layers"]
        total = len(layers)
        head = {
            "party": rep["party"], "year": rep["year"], "url": rep["url"],
            **{k: rep[k] for k in ("corrected_version", "deficient", "listing_note") if k in rep},
            "coverage": {k: v for k, v in cov.items() if k != "layers"},
        }
        if notes:
            head.update(notes)
        if pages is None:
            first = min(thumbnails_from, total)
            numbers = list(range(first, min(total, first + THUMBS_PER_CALL - 1) + 1))
            sheets = await anyio.to_thread.run_sync(reports.contact_sheets, path, numbers)
            head["thumbnails"] = f"pages {reports.ranges(numbers)} of {total}"
            if numbers[-1] < total:
                head["more_thumbnails"] = f"call again with thumbnails_from={numbers[-1] + 1}"
            head["note"] = (
                "Thumbnails only show the layout; read a page with pages='N'. Contents pages (obsah) list the "
                "sections with their page numbers. " + READ_NOTE.split(". Read")[0] + "."
            )
            return [_text(head), *(_image(s) for s in sheets)]

        wanted = reports.parse_pages(pages, total)
        cap = MAX_PAGES_HIGH_DPI if dpi > 150 else MAX_PAGES
        if len(wanted) > cap:
            raise ValueError(f"At most {cap} pages per call at {dpi} dpi; asked for {len(wanted)}.")
        layer_of = {n: layers[n - 1] for n in wanted}
        as_text = [n for n in wanted if layer_of[n] == "T" and not images]
        use_ocr = ocr and reports.tesseract() is not None
        to_ocr = [n for n in wanted if n not in as_text and use_ocr]
        as_image = [n for n in wanted if n not in as_text and n not in to_ocr]
        texts = {n: await anyio.to_thread.run_sync(reports.page_text, path, n) for n in as_text}
        ocred = dict(zip(to_ocr, await anyio.to_thread.run_sync(reports.ocr_pages, path, to_ocr))) if to_ocr else {}
        pictures = dict(zip(as_image, await anyio.to_thread.run_sync(reports.page_images, path, as_image, dpi, rotate))) if as_image else {}

    names = {"T": "text layer", "S": "scanned", "O": "scanned (its scanner OCR layer is garbled, not used)"}
    head["pages"] = [{"page": n, "layer": names[layer_of[n]], "returned_as": "text" if n in texts else "OCR text" if n in ocred else "image"} for n in wanted]
    if pictures:
        head["note"] = IMAGE_NOTE
    if ocr and not use_ocr:
        head["ocr"] = "tesseract with Slovak (slk) is not installed here, so scanned pages are returned as images."
    blocks: list[TextContent | ImageContent] = [_text(head)]
    for n in wanted:
        if n in texts:
            blocks.append(_text(f"--- page {n} (text layer) ---\n{texts[n]}"))
        elif n in ocred:
            blocks.append(_text(f"--- page {n} ({OCR_LABEL}) ---\n{ocred[n]}"))
        else:
            turned = f", turned {rotate}°" if rotate else ""
            blocks.append(_text(f"--- page {n} ({names[layer_of[n]]}; image, {dpi} dpi{turned}) ---"))
            blocks.append(_image(pictures[n]))
    return blocks
