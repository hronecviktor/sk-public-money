"""Tools for Slovak public procurement (verejné obstarávanie): notices from TED."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from . import parse
from .client import MAX_RESULTS, TEDClient

PAGE_SIZE = 20
# eForms became mandatory on TED on 25 October 2023.
EFORMS_FROM = "20231101"
TEXT_MATCH_NOTE = "Found by full text in an older notice, which records no winners for search: check the organisation's role with uvo_get_notice."
TED_NUMBER = re.compile(r"(\d{1,8}-\d{4})")

_client: TEDClient | None = None


def client() -> TEDClient:
    global _client
    if _client is None:
        _client = TEDClient()
    return _client


def _ico_variants(ico: str) -> list[str]:
    ico = ico.replace(" ", "")
    if not ico.isdigit() or len(ico) > 8:
        raise ValueError(f"An IČO has up to 8 digits, got: {ico}")
    # Buyers type IČOs with and without leading zeros; TED compares them exactly.
    return sorted({ico.zfill(8), ico.lstrip("0") or "0"})


def _cpv_prefix(cpv: str) -> str:
    """'09134100-7' -> '091341': the code without check digit and trailing zeros, so a CPV code also
    finds the codes below it."""
    digits = cpv.strip().split("-")[0]
    if not re.fullmatch(r"\d{2,8}", digits):
        raise ValueError(f"A CPV code has 8 digits (optionally with -check digit), got: {cpv}")
    return digits.ljust(8, "0").rstrip("0").ljust(2, "0")


def _ted_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%Y%m%d")


def ted_query(
    *, ico: str | None, role: str, text: str | None, cpv: str | None,
    published_from: str | None, published_to: str | None, awards_only: bool,
) -> str:
    """An expert query for Slovak buyers' notices on TED, newest first."""
    clauses = ["buyer-country=SVK"]
    if ico:
        ids = " ".join(_ico_variants(ico))
        # Notices from before eForms index no winner, so their winners are found by full text.
        legacy = " OR ".join(f"FT~({i})" for i in ids.split())
        winner = f"(winner-identifier IN ({ids}) OR (({legacy}) AND publication-date<{EFORMS_FROM}))"
        by_role = {"buyer": f"buyer-identifier IN ({ids})", "winner": winner}
        clauses.append(by_role.get(role) or f"({by_role['buyer']} OR {winner})")
    if text:
        # TED ignores case and diacritics itself; its query syntax characters are dropped.
        words = re.sub(r"[()\"'~=<>*?{}\[\]\\]", " ", text).split()
        if words:
            clauses.append(f"FT~({' '.join(words)})")
    if cpv:
        clauses.append(f"classification-cpv={_cpv_prefix(cpv)}*")
    if published_from:
        clauses.append(f"publication-date>={_ted_date(published_from)}")
    if published_to:
        clauses.append(f"publication-date<={_ted_date(published_to)}")
    if awards_only:
        clauses.append("form-type=result")
    return " AND ".join(clauses) + " SORT BY publication-date DESC"


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def uvo_search_procurements(
    ico: Annotated[str | None, Field(description="IČO of a buyer or tenderer (up to 8 digits; leading zeros optional).")] = None,
    role: Annotated[Literal["any", "buyer", "winner"], Field(description="With ico: find procurements this organisation ran (buyer), won (winner), or either.")] = "any",
    text: Annotated[str | None, Field(description="Words that must all occur in the notice, ignoring case and diacritics.")] = None,
    cpv: Annotated[str | None, Field(description="CPV code (e.g. 09134100 for diesel); also finds the codes below it, so 09000000 finds all fuels.")] = None,
    published_from: Annotated[str | None, Field(description="Published on or after this date, YYYY-MM-DD.")] = None,
    published_to: Annotated[str | None, Field(description="Published on or before this date, YYYY-MM-DD.")] = None,
    awards_only: Annotated[bool, Field(description="Only result notices (who won, for how much); false also returns calls for tenders, corrections and other notices.")] = True,
    page: Annotated[int, Field(ge=0, description="0-based result page; 20 results per page.")] = 0,
) -> dict:
    """Search Slovak public procurement notices, newest first: buyer and winners with IČO, procedure
    type, CPV, estimated and final value, number of tenders received, contract numbers. Use
    uvo_get_notice with an ID for each lot's winners, bids and contracts with their CRZ IDs.
    Covers procurements above the EU thresholds, published on TED (from 2016). Notices before
    November 2023 record winners only in their text, so winner searches find them by full text."""
    if (page + 1) * PAGE_SIZE > MAX_RESULTS:
        raise ValueError(f"TED returns at most {MAX_RESULTS} results per search; narrow it down by date.")
    query = ted_query(ico=ico, role=role, text=text, cpv=cpv, published_from=published_from,
                      published_to=published_to, awards_only=awards_only)
    data = await client().search(query, parse.SEARCH_FIELDS, page=page + 1, limit=PAGE_SIZE)
    total = data.get("totalNoticeCount") or 0
    rows = [parse.search_row(n) for n in data.get("notices", [])]
    if ico and role != "buyer":
        wanted = set(_ico_variants(ico))
        for row in rows:
            found = {p.get("ico") for p in row.get("buyers", []) + row.get("winners", [])}
            if not wanted & found:
                row["matched_by"] = TEXT_MATCH_NOTE
    return {
        "total": total,
        "page": page,
        "results": rows,
        "has_more": (page + 1) * PAGE_SIZE < total,
        "note": "Winners are matched to their IČOs in the order TED lists them; uvo_get_notice reads the notice itself.",
    }


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def uvo_get_notice(
    notice_id: Annotated[str, Field(description="Notice ID from uvo_search_procurements (ted:682997-2026), a TED publication number (682997-2026) or a TED notice URL.")],
) -> dict:
    """One procurement notice in full: buyers (with contacts and buyer profile), procedure, CPV,
    estimated value, and per lot the result: status, tenders received by type, lowest and highest bid,
    each tender with its tenderers (IČO, size, consortium members, subcontractors), its amount and
    whether it won, and the signed contracts with number, dates and CRZ ID (for crz_get_contract)."""
    m = TED_NUMBER.search(notice_id.strip())
    if not m:
        raise ValueError(f"Not a notice ID: {notice_id}. Expected e.g. ted:682997-2026.")
    number = m.group(1)
    number = f"{int(number.split('-')[0])}-{number.split('-')[1]}"
    return parse.parse_notice(await client().notice_xml(number), number)
