"""Tools for Slovak court data: decisions and judges from the Ministry of Justice (obcan.justice.sk),
judges' asset declarations from Súdna rada (sudnarada.gov.sk) and the register of disqualified persons."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated

from pydantic import Field

from ... import localdb
from ...app import READ_ONLY, mcp, user_errors
from ...text import fold
from ..crz.pdf import extract_text
from . import disqualified, parse
from .client import CourtClient

MAX_WINDOW = 500  # the API's largest page; IČO searches merge two result lists within it
DECLARATION_PAGES = 4  # Súdna rada lists 25 declarations a page

_client: CourtClient | None = None


def client() -> CourtClient:
    global _client
    if _client is None:
        _client = CourtClient()
    return _client


def ico_forms(ico: str) -> list[str]:
    """Query strings for an IČO as decisions print it: '31322832' and the phrase '"31 322 832"'.
    For IČOs with leading zeros the phrase drops them ('"604 381"'), which also matches '00 604 381'."""
    digits = ico.replace(" ", "")
    if not digits.isdigit() or len(digits) > 8:
        raise ValueError(f"An IČO has up to 8 digits, got: {ico}")
    digits = digits.zfill(8)
    stripped = digits.lstrip("0") or "0"
    groups = []
    while stripped:
        groups.insert(0, stripped[-3:])
        stripped = stripped[:-3]
    return [digits, '"' + " ".join(groups) + '"']


def _iso(value: str | None, field: str) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{field} must be a date as YYYY-MM-DD, got: {value}") from None


async def _court_id(court: str) -> str:
    court = court.strip()
    if re.fullmatch(r"sud_\d+", court):
        return court
    data = await client().api("sud", {"query": court, "zahrnutZaniknuteSudy": "true", "size": 20, "page": 1})
    found = [(c.get("nazov") or "", c.get("registreGuid")) for c in data.get("sudList") or []]
    wanted = " ".join(fold(court).split())
    exact = [f for f in found if " ".join(fold(f[0]).split()) == wanted]
    if len(exact) == 1 or len(found) == 1:
        return (exact or found)[0][1]
    if not found:
        raise LookupError(f"No court named like {court!r}. Use the name as in 'Okresný súd Trenčín' or 'Krajský súd v Žiline'.")
    names = "; ".join(f"{n} ({i})" for n, i in found[:10])
    raise ValueError(f"Several courts match {court!r}: {names}. Pass one name or its ID.")


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def court_search_decisions(
    ico: Annotated[str | None, Field(description="IČO of a company; finds decisions whose text contains it, written either as 31322832 or as 31 322 832.")] = None,
    text: Annotated[str | None, Field(description="Words to find in the decision text, case number or judge name. Every word must occur; words are stemmed, so results can be loose. Use \"quotes\" for an exact phrase.")] = None,
    court: Annotated[str | None, Field(description="Court name (e.g. 'Okresný súd Trenčín', 'Najvyšší súd') or court ID such as sud_153.")] = None,
    judge_id: Annotated[str | None, Field(description="Judge ID such as sudca_1990, from court_judge or an earlier result.")] = None,
    area: Annotated[str | None, Field(description="Legal area: Občianske, Rodinné, Obchodné, Trestné or Správne právo, or a sub-area such as Konkurz, Hospodárske trestné činy, Zmluva o dielo (any case and diacritics).")] = None,
    date_from: Annotated[str | None, Field(description="Decisions issued on or after this day, YYYY-MM-DD.")] = None,
    date_to: Annotated[str | None, Field(description="Decisions issued on or before this day, YYYY-MM-DD.")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    page: Annotated[int, Field(ge=1, description="Page of results, 1-based.")] = 1,
) -> dict:
    """Search Slovak court decisions (rozhodnutia of all general courts since about 2000, ~4.7M),
    newest first. Parties who are people are anonymised; companies keep name and IČO. Each hit gives
    court, judge, case number, form and text snippets with the match marked «like this». The API has
    no full text: use court_get_decision for metadata, cited laws and the decision's PDF text."""
    if not ico and not text and not judge_id:
        raise ValueError("Give ico, text or judge_id.")
    params: dict = {"sortProperty": "datumVydania", "sortDirection": "DESC"}
    if court:
        params["guidSud"] = await _court_id(court)
    if judge_id:
        params["guidSudca"] = judge_id.strip()
    if area:
        kind, value = parse.match_area(area)
        params["oblastPravnejUpravyFacetFilter" if kind == "oblast" else "podOblastPravnejUpravyFacetFilter"] = value
    if date_from:
        params["vydaniaOd"] = _iso(date_from, "date_from")
    if date_to:
        params["vydaniaDo"] = _iso(date_to, "date_to")

    text = (text or "").strip()
    queries = [f"{form} {text}".strip() for form in ico_forms(ico)] if ico else [text]
    if len(queries) == 1:
        if queries[0]:
            params["query"] = queries[0]
        data = await client().api("rozhodnutie", {**params, "size": limit, "page": page})
        total = data.get("numFound") or 0
        out = {
            "matching": total,
            "decisions": [parse.decision(r) for r in data.get("rozhodnutieList") or []],
            "has_more": page * limit < total,
            **parse.facets(data),
        }
    else:
        # Each spelling of the IČO is a separate search; merge them newest first.
        window = page * limit
        if window > MAX_WINDOW:
            raise ValueError(f"IČO searches can page through the newest {MAX_WINDOW} decisions only; narrow by date, court or area.")
        merged: dict[str, dict] = {}
        counts = {}
        breakdown: dict[str, dict[str, int]] = {}
        for q in queries:
            data = await client().api("rozhodnutie", {**params, "query": q, "size": window, "page": 1})
            counts[q] = data.get("numFound") or 0
            for rec in data.get("rozhodnutieList") or []:
                merged.setdefault(rec["guid"], rec)
            for key, values in parse.facets(data).items():
                for name, n in values.items():
                    breakdown.setdefault(key, {})[name] = breakdown.get(key, {}).get(name, 0) + n
        hits = sorted((parse.decision(r) for r in merged.values()), key=lambda d: d["date"] or "", reverse=True)
        out = {
            # A decision can contain both spellings, so the sum is an upper bound.
            "matching": {"by_spelling": counts, "at_most": sum(counts.values())},
            "decisions": hits[window - limit : window],
            "has_more": len(hits) > window or any(n > window for n in counts.values()),
            **breakdown,
            "matched_by": "full text: the IČO occurs in the decision; check the snippet, it may be another party's number",
        }
    out["sources"] = "Ministerstvo spravodlivosti SR, obcan.justice.sk (pilot API, no licence published)"
    return out


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def court_get_decision(
    decision: Annotated[str, Field(description="Decision guid from court_search_decisions, or its ECLI (ECLI:SK:...).")],
    text: Annotated[bool, Field(description="Also download the decision PDF and return its text.")] = False,
    start_page: Annotated[int, Field(ge=1, description="With text: first PDF page to return.")] = 1,
    max_pages: Annotated[int, Field(ge=1, le=200, description="With text: maximum number of pages.")] = 20,
    max_chars: Annotated[int, Field(ge=1000, le=200_000, description="With text: maximum characters of text.")] = 40_000,
) -> dict:
    """One court decision: court, judge, case and file number, ECLI, form and nature (e.g. confirmed,
    overturned), legal area, cited laws (Slov-Lex links) and the PDF link; with text=true also the
    PDF text page by page. Names of people in the text are anonymised by the court."""
    decision = decision.strip()
    if decision.upper().startswith("ECLI:"):
        found = await client().api("rozhodnutie", {"ecli": decision, "size": 2, "page": 1})
        rows = found.get("rozhodnutieList") or []
        if not rows:
            raise LookupError(f"No decision with ECLI {decision}.")
        decision = rows[0]["guid"]
    out = parse.decision(await client().api(f"rozhodnutie/{decision}"))
    if text:
        if not out.get("pdf"):
            out["text"] = {"note": "The register has no PDF for this decision."}
        else:
            data = await client().pdf(out["pdf"]["url"])
            out["text"] = extract_text(data, start_page=start_page, max_pages=max_pages, max_chars=max_chars)
    return out


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def court_judge(
    name: Annotated[str | None, Field(description="Judge's surname, or first name and surname, in any case and with or without diacritics.")] = None,
    declaration_url: Annotated[str | None, Field(description="Instead of a search: an asset-declaration PDF URL from an earlier result, to read its text.")] = None,
    declarations: Annotated[bool, Field(description="Also list the judge's asset declarations (majetkové priznania, 2013 onwards).")] = True,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> dict:
    """Find judges: court, function and status (active, removed, suspended), judge_id for
    court_search_decisions, and their yearly asset declarations published by Súdna rada (property,
    vehicles, income from the office, debts, declared conflicts). Pass declaration_url to read one
    declaration's text. Declarations are matched to judges by name only."""
    if declaration_url:
        data = await client().pdf(declaration_url)
        return {"url": declaration_url, **extract_text(data, max_pages=50, max_chars=60_000)}
    if not name or len(name.strip()) < 2:
        raise ValueError("Give a judge's name, or a declaration_url.")
    name = " ".join(name.split())

    data = await client().api("sudca", {"query": name, "size": limit, "page": 1})
    if not data.get("numFound") and fold(name) != name.lower():
        data = await client().api("sudca", {"query": fold(name), "size": limit, "page": 1})
    judges = [parse.judge(r) for r in data.get("sudcaList") or []]
    out: dict = {"matching": data.get("numFound") or 0, "judges": judges, "has_more": (data.get("numFound") or 0) > len(judges)}

    if declarations:
        # Súdna rada matches one word against surnames only (ignoring diacritics), so try the last
        # word first ("Richard Klimek"), then the others ("Klimek Richard").
        words = fold(name).split()
        kept, rows, total, fragment = [], [], None, words[-1]
        for fragment in [words[-1]] + sorted(words[:-1], key=len, reverse=True):
            rows, total = [], None
            for p in range(DECLARATION_PAGES):
                page_rows, total, has_next = parse.declarations(await client().declarations_page(fragment, p))
                rows += page_rows
                if not has_next:
                    break
            kept = [r for r in rows if r["name"] and parse.name_matches(r["name"], name)]
            if kept:
                break
        kept.sort(key=lambda r: (fold(r["name"]), -(r["year"] or 0)))
        out["asset_declarations"] = kept
        out["asset_declarations_matched_by"] = "name"
        if total is not None and total > len(rows):
            out["asset_declarations_note"] = f"Only the first {len(rows)} of {total} declarations matching '{fragment}' were checked; give a longer name."
    out["sources"] = "Ministerstvo spravodlivosti SR (obcan.justice.sk) and Súdna rada SR (sudnarada.gov.sk); no licence published"
    return out


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def court_disqualified(
    name: Annotated[str | None, Field(description="Person's name or surname, in any case and with or without diacritics; every word must match the start of a name word.")] = None,
    record_id: Annotated[str | None, Field(description="Instead of a name: a record ID such as diskvalifikacia_1400, from a 'disqualified' flag in RPO results.")] = None,
    active_only: Annotated[bool, Field(description="Only bans in force today.")] = False,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> dict:
    """The register of disqualified persons (register diskvalifikácií): people banned by a court or
    tax office from statutory bodies of companies (konateľ, predstavenstvo, prokurista). Each record
    gives name, address, decision date, court, case number, the ban period and whether it is in force
    today. Matched by name only; the register has no birth dates."""
    if not name and not record_id:
        raise ValueError("Give a name or a record_id.")
    db = disqualified.open_db()
    try:
        note = await disqualified.ensure_fresh(db, client())
        if record_id:
            rows = db.execute("SELECT * FROM disqualified WHERE id = ?", (record_id.strip(),)).fetchall()
            if not rows:
                raise LookupError(f"No record {record_id} in the register of disqualified persons; it may have been removed.")
        else:
            rows = disqualified.search(db, name)
        total = len(rows)
        # Without active_only only the shown records need their detail (ban period).
        wanted = rows if active_only else rows[:limit]
        await disqualified.fetch_details(db, client(), [r["id"] for r in wanted])
        ids = [r["id"] for r in wanted]
        fresh = {r["id"]: r for r in db.execute(f"SELECT * FROM disqualified WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        records = [disqualified.record(fresh[i]) for i in ids if i in fresh]
        if active_only:
            records = [r for r in records if r.get("ban_active")]
            total = len(records)
        out: dict = {"matching": total, "records": records[:limit], "has_more": total > limit, "matched_by": "name"}
        last = localdb.get_meta(db, "list")
        if last:
            out["register_copy_from"] = datetime.fromtimestamp(float(last)).strftime("%Y-%m-%d %H:%M")
        if note:
            out["note"] = note
        out["sources"] = "Ministerstvo spravodlivosti SR, register diskvalifikácií (obcan.justice.sk); no licence published"
        return out
    finally:
        db.close()
