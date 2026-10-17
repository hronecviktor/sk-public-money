"""Tools for REPLIK, the register of insolvency, restructuring and liquidation proceedings
(Register predinsolvenčných, likvidačných a insolvenčných konaní), live from its public SOAP API.

A proceeding moved to another court keeps one REPLIK ID but its notices are listed under the old
court code and file number by IČO and under the current ones by file number. insolvency_get reads
them by the current file number, which returns the full history, and falls back to the debtor's
IČO when that finds nothing.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from . import parse
from .client import PAGE_MAX, ReplikClient

FULL_TEXT_MAX = 10  # notices whose text one insolvency_get call reads
TEXT_LIMIT_MANY = 6_000  # characters per notice when several are read
TEXT_LIMIT_ONE = 60_000  # characters of a single notice
ICO_FALLBACK_MAX = 300  # notices read by IČO when the file number finds none
NAME_NOTE = ("Found by full text (names, file numbers, IČO); a matching name is not proof of the same "
             "person or company, so compare IČO or birth date.")
COURT_NOTE = ("Courts were reorganised in 2023: a proceeding moved to a new court keeps its REPLIK ID and gets "
              "a new file number (e.g. 27K/14/2021 at Okresný súd Bratislava I became B1-27K/14/2021 at "
              "Mestský súd Bratislava III).")

_client: ReplikClient | None = None


def client() -> ReplikClient:
    global _client
    if _client is None:
        _client = ReplikClient()
    return _client


def normalize_ico(ico: str) -> str:
    ico = ico.replace(" ", "")
    if not ico.isdigit() or len(ico) > 8:
        raise ValueError(f"An IČO has up to 8 digits, got: {ico}")
    return ico.zfill(8)


async def _paged(fetch, limit: int, offset: int) -> tuple[list[dict], int]:
    """Rows offset..offset+limit of a paged REPLIK list, and its total."""
    size = min(PAGE_MAX, max(limit, 1))
    if offset % size:
        size = PAGE_MAX  # read whole pages and cut locally when the offset does not align
    page, skip = divmod(offset, size)
    rows, total = await fetch(page, size)
    rows = rows[skip:]
    while len(rows) < limit and (page + 1) * size < total:
        page += 1
        more, total = await fetch(page, size)
        if not more:
            break
        rows.extend(more)
    return rows[:limit], total


async def _newest_notices(fetch, limit: int) -> tuple[list[dict], int]:
    """The newest `limit` notices, newest first. REPLIK lists notices oldest first, so after the
    first page (which gives the total) the last pages are read, backwards."""
    first, total = await fetch(0, PAGE_MAX)
    if total <= PAGE_MAX:
        rows = first
    else:
        last_page = (total - 1) // PAGE_MAX
        rows: list[dict] = []
        for page in range(last_page, 0, -1):
            chunk, _ = await fetch(page, PAGE_MAX)
            rows = chunk + rows
            if len(rows) >= limit:
                break
        else:
            rows = first + rows
    notices = [parse.notice_info(r) for r in rows]
    notices.sort(key=lambda n: (n.get("date") or "", n.get("id") or 0), reverse=True)
    return notices[:limit], total


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def insolvency_search(
    ico: Annotated[str | None, Field(description="Debtor's IČO (spaces allowed; shorter ones are zero-padded).")] = None,
    text: Annotated[str | None, Field(min_length=3, description="Full text: a company or person name (any case and diacritics), a file number such as B1-27K/14/2021, or an IČO.")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    offset: Annotated[int, Field(ge=0, description="Skip this many results (for paging).")] = 0,
) -> dict:
    """Find insolvency proceedings in REPLIK (the Ministry of Justice register that replaced Register
    úpadcov in October 2025, with its history): bankruptcy (konkurz), restructuring, personal debt
    relief (oddlženie), liquidation by court, preventive restructuring. Each hit gives the REPLIK ID,
    court and file number, type, status, debtor (with IČO, or birth date for people), dates and the
    trustee. Newest activity first. Use insolvency_get for parties and notices."""
    if (ico is None) == (text is None):
        raise ValueError("Give exactly one of ico or text.")
    if ico is not None:
        wanted = normalize_ico(ico)
        rows, total = await _paged(lambda p, s: client().proceedings_by_ico(wanted, p, s), limit, offset)
        if not total and wanted.startswith("0"):
            short = wanted.lstrip("0") or "0"
            rows, total = await _paged(lambda p, s: client().proceedings_by_ico(short, p, s), limit, offset)
        matched_by = "ico"
    else:
        rows, total = await _paged(lambda p, s: client().search_proceedings(text.strip(), p, s), limit, offset)
        matched_by = "text"
    result = {
        "matched_by": matched_by,
        "total": total,
        "proceedings": [parse.proceeding_info(r) for r in rows],
        "has_more": offset + len(rows) < total,
    }
    if matched_by == "text":
        result["note"] = NAME_NOTE
    if not total:
        result["note"] = ("No proceeding in REPLIK" + (f" for IČO {normalize_ico(ico)}." if ico else ".")
                          + " REPLIK covers court insolvency proceedings and court-ordered liquidations; voluntary "
                          "liquidations of companies are announced in the Obchodný vestník.")
    return result


async def _status(detail: dict) -> dict | None:
    """The search row of a proceeding (status, end date, last event), which the detail lacks."""
    ico, file_number = (detail.get("debtor") or {}).get("ico"), detail.get("file_number")
    if ico:
        rows, _ = await client().proceedings_by_ico(ico, 0, PAGE_MAX)
    elif file_number:
        rows, _ = await client().search_proceedings(file_number, 0, PAGE_MAX)
    else:
        return None
    return next((parse.proceeding_info(r) for r in rows if parse._int(r.get("Id")) == detail.get("id")), None)


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def insolvency_get(
    proceeding_id: Annotated[int | None, Field(description="REPLIK proceeding ID, as returned by insolvency_search.")] = None,
    notice_id: Annotated[int | None, Field(description="ID of one notice, to read its full text.")] = None,
    notices: Annotated[int, Field(ge=0, le=500, description="How many notices of the proceeding to list, newest first.")] = 30,
    full_text: Annotated[bool, Field(description=f"Also read the text of the newest notices (at most {FULL_TEXT_MAX}, each cut to {TEXT_LIMIT_MANY} characters). Slow: about 1 s per notice.")] = False,
) -> dict:
    """A REPLIK insolvency proceeding: debtor and petitioners (name, IČO or birth date, address),
    court, judge, trustee (správca) with contacts, type, status and key dates, and its public
    notices (court decisions and trustee announcements: creditor meetings, asset lists, auctions,
    distribution) newest first. With notice_id, the full text of one notice instead."""
    if (proceeding_id is None) == (notice_id is None):
        raise ValueError("Give exactly one of proceeding_id or notice_id.")

    if notice_id is not None:
        raw = await client().notice(notice_id)
        if raw is None:
            raise LookupError(f"No notice {notice_id} in REPLIK.")
        return {"notice": parse.notice(raw, limit=TEXT_LIMIT_ONE)}

    raw = await client().proceeding(proceeding_id)
    if raw is None:
        raise LookupError(f"No proceeding {proceeding_id} in REPLIK.")
    detail = parse.proceeding(raw)
    status = await _status(detail)
    if status:
        for key in ("status", "ended", "end_reason", "last_event", "last_event_date", "filed", "disputed_claims"):
            if key in status:
                detail[key] = status[key]
    else:
        detail["status"] = None
        detail["status_note"] = "REPLIK's search did not return this proceeding, so its status and end date are unknown."

    result: dict = {"proceeding": detail}
    if notices:
        file_number, court_code = detail.get("file_number"), detail.get("court_code")
        listed, total = [], 0
        if file_number and court_code:
            listed, total = await _newest_notices(
                lambda p, s: client().notices_by_file(file_number, court_code, p, s), notices
            )
        debtor_ico = (detail.get("debtor") or {}).get("ico")
        if not total and debtor_ico:
            # By IČO REPLIK lists the notices of all the debtor's proceedings; keep this one's.
            listed, _ = await _newest_notices(lambda p, s: client().notices_by_ico(debtor_ico, p, s), ICO_FALLBACK_MAX)
            listed = [n for n in listed if n.get("proceeding_id") == proceeding_id]
            total = len(listed)
            listed = listed[:notices]
        if full_text:
            texts = []
            for n in listed[:FULL_TEXT_MAX]:
                raw_notice = await client().notice(n["id"])
                texts.append(parse.notice(raw_notice, limit=TEXT_LIMIT_MANY) if raw_notice else n)
            listed = texts + listed[FULL_TEXT_MAX:]
        result["notices_total"] = total
        result["notices"] = listed
        if any(n.get("court_code") != court_code for n in listed):
            result["note"] = COURT_NOTE
    return result
