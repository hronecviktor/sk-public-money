"""Tools for Slovak public procurement (verejné obstarávanie): the Vestník index and TED.

The local Vestník index (see sync.py) holds every notice ÚVO published since it moved to eForms in
October 2023, below the EU thresholds too, and the half-year reports of low-value contracts. TED
holds the procurements above the EU thresholds back to 2016. A search reads the index for the dates
it covers and TED for the years before, newest first; TED notices that are also in the index are
dropped. Without an index, everything comes from TED.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from . import parse, sync
from .client import MAX_RESULTS, TEDClient
from .index import VestnikIndex, default_path

PAGE_SIZE = 20
# eForms became mandatory on TED on 25 October 2023.
EFORMS_FROM = "20231101"
TEXT_MATCH_NOTE = "Found by full text in an older notice, which records no winners for search: check the organisation's role with uvo_get_notice."
ROLE_NOTE = "The organisation is in this notice as a losing tenderer or a subcontractor, which search results do not list; see uvo_get_notice."
TED_ROLE_NOTES = {
    "tenderer": "TED indexes losing tenderers only in eForms notices (from November 2023); its older notices are found only where the organisation won.",
    "subcontractor": "TED indexes subcontractors only in eForms notices (from November 2023).",
}
NO_INDEX_NOTE = ("Only TED was searched (procurements above the EU thresholds): the local Vestník index, which adds "
                 "procurements below them and low-value contracts, has not been built. Build it with sk-public-money-sync uvo.")
TED_NUMBER = re.compile(r"(\d{1,8}-\d{4})")
VESTNIK_ID = re.compile(r"vestnik:\d+(?:-\d+)?")

_client: TEDClient | None = None
_catalogue: sync.CatalogueClient | None = None
_index: VestnikIndex | None = None


def client() -> TEDClient:
    global _client
    if _client is None:
        _client = TEDClient()
    return _client


def catalogue() -> sync.CatalogueClient:
    global _catalogue
    if _catalogue is None:
        _catalogue = sync.CatalogueClient()
    return _catalogue


def index() -> VestnikIndex | None:
    """The Vestník index, or None while it has not been built."""
    global _index
    if _index is None and default_path().exists():
        _index = VestnikIndex()
    return _index if _index is not None and _index.coverage() else None


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
        buyer = f"buyer-identifier IN ({ids})"
        by_role = {
            "any": f"({buyer} OR {winner})",
            "buyer": buyer,
            "winner": winner,
            # Indexed on eForms notices only; older notices name only their winners.
            "tenderer": f"(organisation-identifier-tenderer IN ({ids}) OR {winner})",
            "subcontractor": f"organisation-identifier-subcontractor IN ({ids})",
        }
        clauses.append(by_role[role])
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


async def _ted_rows(query: str, offset: int, count: int) -> tuple[list[dict], int]:
    """count TED hits from offset on (TED pages by 20, so this may take two requests), and the total."""
    first = offset // PAGE_SIZE
    if (first + 2) * PAGE_SIZE > MAX_RESULTS:
        raise ValueError(f"TED returns at most {MAX_RESULTS} results per search; narrow it down by date.")
    data = await client().search(query, parse.SEARCH_FIELDS, page=first + 1, limit=PAGE_SIZE)
    notices = data.get("notices", [])
    skip = offset - first * PAGE_SIZE
    if count and skip + count > PAGE_SIZE and len(notices) == PAGE_SIZE:
        more = await client().search(query, parse.SEARCH_FIELDS, page=first + 2, limit=PAGE_SIZE)
        notices += more.get("notices", [])
    return [parse.search_row(n) for n in notices[skip : skip + count]], data.get("totalNoticeCount") or 0


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def uvo_search_procurements(
    ico: Annotated[str | None, Field(description="IČO of a buyer, tenderer or supplier (up to 8 digits; leading zeros optional).")] = None,
    role: Annotated[Literal["any", "buyer", "winner", "tenderer", "subcontractor"], Field(description="With ico: procurements this organisation ran (buyer) or won (winner, including suppliers in low-value contract reports); any = either. tenderer: bid in, whether it won or not. subcontractor: named as a tenderer's subcontractor.")] = "any",
    text: Annotated[str | None, Field(description="Words that must all occur in the notice, ignoring case and diacritics; word endings may differ (nafta finds nafty).")] = None,
    cpv: Annotated[str | None, Field(description="CPV code (e.g. 09134100 for diesel); also finds the codes below it, so 09000000 finds all fuels.")] = None,
    published_from: Annotated[str | None, Field(description="Published on or after this date, YYYY-MM-DD.")] = None,
    published_to: Annotated[str | None, Field(description="Published on or before this date, YYYY-MM-DD.")] = None,
    awards_only: Annotated[bool, Field(description="Only results: award notices and low-value contract reports. False also returns calls for tenders, corrections, contract changes and other notices.")] = True,
    source: Annotated[Literal["all", "vestnik", "ted"], Field(description="all: the Vestník index for the dates it covers, TED before; vestnik or ted: only that source.")] = "all",
    page: Annotated[int, Field(ge=0, description="0-based result page; 20 results per page.")] = 0,
) -> dict:
    """Search Slovak public procurement, newest first: buyer and winners with IČO, procedure type,
    CPV, estimated and final value, number of tenders received, contract numbers and CRZ IDs. Use
    uvo_get_notice with an ID for each lot's tenders, winners and contracts.

    Sources: the Vestník verejného obstarávania (ÚVO's journal, every notice since October 2023,
    also below the EU thresholds, plus half-year reports of low-value contracts) and TED (notices
    above the EU thresholds back to 2016). Each result names its source."""
    for value in (published_from, published_to):
        if value:
            date.fromisoformat(value)
    icos = _ico_variants(ico) if ico else None
    ix = index() if source != "ted" else None
    out: dict = {}
    if source == "vestnik" and ix is None:
        raise ValueError("The Vestník index has not been built; build it with sk-public-money-sync uvo, or search source=ted.")
    if ix is not None:
        out.update(await sync.refresh(ix, catalogue()) or {})

    offset = page * PAGE_SIZE
    rows: list[dict] = []
    v_total = t_total = 0
    if ix is not None:
        rows, v_total = ix.search(
            icos=[icos[0]] if icos else None, role=role, text=text, cpv_prefix=_cpv_prefix(cpv) if cpv else None,
            published_from=published_from, published_to=published_to, awards_only=awards_only,
            limit=PAGE_SIZE, offset=offset,
        )
        if icos:
            matched = ix.roles_of([r["id"] for r in rows], icos[0])
            for r in rows:
                r["matched_as"] = matched.get(r["id"], [])

    if source != "vestnik":
        ted_to = published_to
        if ix is not None:
            # TED for the years before the index begins.
            before = (date.fromisoformat(ix.coverage()["from"]) - timedelta(days=1)).isoformat()
            ted_to = min(published_to, before) if published_to else before
        if not (published_from and ted_to and published_from > ted_to):
            query = ted_query(ico=ico, role=role, text=text, cpv=cpv, published_from=published_from,
                              published_to=ted_to, awards_only=awards_only)
            need = PAGE_SIZE - len(rows)
            ted, t_total = await _ted_rows(query, max(0, offset - v_total), need)
            known = ix.has_uuids([r["notice_uuid"] for r in ted if r.get("notice_uuid")]) if ix else set()
            for r in ted:
                if r.get("notice_uuid") in known:
                    continue
                if icos and role != "buyer" and not set(icos) & {p.get("ico") for p in r.get("buyers", []) + r.get("winners", [])}:
                    r["matched_by"] = TEXT_MATCH_NOTE if (r.get("published") or "") < "2023-11-01" else ROLE_NOTE
                rows.append(r)

    total = v_total + t_total
    out = {
        "total": total,
        "page": page,
        "results": rows,
        "has_more": offset + PAGE_SIZE < total,
        **({"vestnik_index": ix.coverage()} if ix else {}),
        **out,
    }
    if ix is None and source == "all":
        out["note"] = NO_INDEX_NOTE
    if t_total:
        out["ted_note"] = "TED results pair winners with IČOs in the order TED lists them; uvo_get_notice reads the notice itself."
        if icos and role in TED_ROLE_NOTES:
            out["ted_note"] += " " + TED_ROLE_NOTES[role]
    return out


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def uvo_get_notice(
    notice_id: Annotated[str, Field(description="Notice ID from uvo_search_procurements (vestnik:1420344, vestnik:1285290-2, ted:682997-2026), a TED publication number or a TED notice URL.")],
) -> dict:
    """One procurement notice in full: buyers (with contacts and buyer profile), procedure, CPV,
    estimated value, and per lot the result: status, tenders received by type, lowest and highest bid,
    each tender with its tenderers (IČO, size, consortium members, subcontractors), its amount and
    whether it won, and the signed contracts with number, dates and CRZ ID (for crz_get_contract).
    A low-value contract report entry lists its contracts with suppliers and values without VAT."""
    vestnik_id = VESTNIK_ID.search(notice_id.strip())
    if vestnik_id:
        ix = index()
        if ix is None:
            raise ValueError("The Vestník index has not been built; build it with sk-public-money-sync uvo.")
        notice = ix.get(vestnik_id.group(0))
        if notice is None:
            raise LookupError(f"No notice {vestnik_id.group(0)} in the Vestník index.")
        return notice
    m = TED_NUMBER.search(notice_id.strip())
    if not m:
        raise ValueError(f"Not a notice ID: {notice_id}. Expected e.g. vestnik:1420344 or ted:682997-2026.")
    number = m.group(1)
    number = f"{int(number.split('-')[0])}-{number.split('-')[1]}"
    return parse.parse_notice(await client().notice_xml(number), number)
