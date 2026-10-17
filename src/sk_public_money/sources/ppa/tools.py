"""Tools for the CAP beneficiaries published by Pôdohospodárska platobná agentúra (PPA): who got
how much farm and rural-development money (EAGF = EPZF, EAFRD = EPFRV, plus national
co-financing), under which measure, in which obec. The lists come from a local index (sync.py).

PPA publishes no IČO, so beneficiaries are matched to companies by name only, and every result says so.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

from pydantic import Field

from ... import localdb
from ...app import READ_ONLY, mcp, user_errors
from ...text import fold
from ..rpo import parse as rpo_parse
from ..rpo.tools import client as rpo_client
from ..rpo.tools import name_key, normalize_ico
from .index import PPAIndex, default_path
from .parse import FUNDS, MEASURES, OBJECTIVES, measure_code, place_key

STALE_AFTER = 45 * 86400
NOT_BUILT = ("The PPA beneficiary index has not been built yet. Build it with: sk-public-money-sync ppa "
             "(downloads about 16 MB from www.apa.sk and takes about a minute).")
NAME_NOTE = ("PPA publishes no IČO; ico is filled only when you searched by IČO, and then the beneficiary "
             "merely carries the same name (matched_by: name), which is not proof of the same entity.")
ANONYMISED_NOTE = ("Recipients of EUR 1,250 or less in a year are published only as a numeric code (kind: anonymised), "
                   "and where a small obec would identify a person, PPA gives the okres instead (\"Okres X\").")
Kind = Literal["natural_person", "entity", "anonymised"]

_index: PPAIndex | None = None


def index() -> PPAIndex:
    global _index
    if _index is None:
        if not localdb.exists(default_path()):
            raise LookupError(NOT_BUILT)
        _index = PPAIndex()
    if not _index.years():
        raise LookupError(NOT_BUILT)
    return _index


def _year(idx: PPAIndex, year: int | None) -> int | None:
    years = idx.years()
    if year is not None and year not in years:
        raise ValueError(f"Financial year {year} is not in the index; it holds {', '.join(map(str, years))}.")
    return year


def _notes(idx: PPAIndex) -> dict:
    out = {"notes": [ANONYMISED_NOTE]}
    checked = idx.get_meta("checked_at")
    if checked and time.time() - float(checked) > STALE_AFTER:
        out["index_note"] = ("The index was last checked against www.apa.sk on "
                             f"{time.strftime('%Y-%m-%d', time.localtime(float(checked)))}; run sk-public-money-sync ppa for newer lists.")
    return out


def _money(row: dict) -> dict:
    """Total and the non-zero parts: EPZF (EAGF), EPFRV (EAFRD) and national co-financing."""
    out = {"total": row["total"]}
    out.update({f: row[f] for f in FUNDS if row.get(f)})
    return out


def _by_measure(amounts: list[dict]) -> list[dict]:
    merged: dict[str | None, dict] = {}
    for a in amounts:
        m = merged.setdefault(a["code"], {"code": a["code"], "objectives": [], **{f: 0.0 for f in (*FUNDS, "total")}, "operations": 0})
        for f in (*FUNDS, "total"):
            m[f] += a[f]
        m["operations"] += a["operations"]
        for o in (a["objectives"] or "").split(", "):
            if o and o not in m["objectives"]:
                m["objectives"].append(o)
    out = []
    for m in sorted(merged.values(), key=lambda m: -m["total"]):
        row = {"code": m["code"], "measure": MEASURES.get(m["code"]) if m["code"] else "no measure code published"}
        row.update(_money({f: round(m[f], 2) for f in (*FUNDS, "total")}))
        row["operations"] = m["operations"]
        if m["objectives"]:
            row["objectives"] = [f"{o}: {OBJECTIVES[o]}" if o in OBJECTIVES else o for o in m["objectives"]]
        out.append({k: v for k, v in row.items() if v is not None})
    return out


def _beneficiary(b: dict, amounts: list[dict] | None = None, *, ico: str | None = None) -> dict:
    out = {"fiscal_year": b["year"], "name": b["name"], "kind": b["kind"], "places": b["places"]}
    if b.get("also_named"):
        out["also_named"] = b["also_named"]
    if b.get("parent"):
        out["parent_company"] = b["parent"]
    out["ico"] = ico or b.get("ico")
    out["matched_by"] = "name"
    out.update(_money(b))
    if amounts is not None:
        out["by_measure"] = _by_measure(amounts)
    return out


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def ppa_search_beneficiary(
    name: Annotated[str | None, Field(description="Words of the beneficiary's name or of its parent company, any case and diacritics, each as a word prefix (kovac pet finds Peter Kováč).")] = None,
    ico: Annotated[str | None, Field(description="IČO of a company: its current and former names are looked up in RPO (5-30 s) and beneficiaries with exactly that name are returned.")] = None,
    obec: Annotated[str | None, Field(description="Obec (municipality) of the beneficiary as published; Bratislava also finds Bratislava-Ružinov. Anonymised recipients carry their okres.")] = None,
    year: Annotated[int | None, Field(description="Financial year (FY2025 = payments from 16 Oct 2024 to 15 Oct 2025). Default: every indexed year.")] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
    page: Annotated[int, Field(ge=0, description="0-based page of results.")] = 0,
) -> dict:
    """CAP subsidies paid by PPA to a farmer, company or body: per financial year the total and its
    split into EPZF (EAGF), EPFRV (EAFRD) and national co-financing, by measure code (Annex IX,
    e.g. I.1 basic income support, V.2 areas with natural constraints) with the CAP specific
    objectives, obce, and the parent company where published. PPA publishes no IČO: matches are by
    name only."""
    if (name is None) == (ico is None):
        raise ValueError("Give exactly one of name or ico.")
    idx = index()
    year = _year(idx, year)
    out: dict = {}
    if ico is not None:
        ico = normalize_ico(ico)
        found = await rpo_client().search(identifier=ico)
        if not found:
            raise LookupError(f"No organization with IČO {ico} in RPO, so there is no name to look for.")
        names, seats = [], set()
        for rec in found:
            names += [n["value"] for n in rec.get("fullNames") or [] if n.get("value")]
            seat = rpo_parse.current_value(rec.get("addresses"), rpo_parse.today())
            if seat and (town := rpo_parse.code_value(seat.get("municipality"))):
                seats.add(place_key(town))
        keys = sorted({name_key(n) for n in names} - {""})
        rows = idx.by_name_keys(keys, year=year, place=obec) if keys else []
        summary = rpo_parse.summary(found[0])
        out["rpo"] = {"ico": ico, "name": summary["name"], "seat": summary["address"],
                      "former_names": sorted(set(names) - {summary["name"]})}
        total = len(rows)
        rows = rows[page * limit : (page + 1) * limit]
        results = []
        amounts = idx.amounts([b["id"] for b in rows])
        for b in rows:
            item = _beneficiary(b, amounts[b["id"]], ico=ico)
            item["seat_obec_matches"] = any(place_key(p) in seats for p in b["places"])
            results.append(item)
        if not results:
            out["note"] = "No beneficiary has exactly this organization's name; try a name search with fewer words."
    else:
        rows, total = idx.search(name, year=year, place=obec, limit=limit, offset=page * limit)
        amounts = idx.amounts([b["id"] for b in rows])
        results = [_beneficiary(b, amounts[b["id"]]) for b in rows]
        words = fold(name).split()
        for b, item in zip(rows, results):
            own = fold(" ".join([b["name"], *(b.get("also_named") or [])]))
            if not all(w in own for w in words):
                item["matched_in"] = "parent_company"
    out.update({"matching": total, "beneficiaries": results, "has_more": total > (page + 1) * limit, "match_note": NAME_NOTE})
    out.update(_notes(idx))
    return out


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def ppa_top(
    measure: Annotated[str | None, Field(description="Measure code from Annex IX, e.g. I.1 (basic income support), I.4 (eco-schemes), I.6 (coupled support), V.2 (areas with natural constraints), VI.4 (investments, RDP 2014-2022); a group without the dot (I, V, VI) counts all its codes.")] = None,
    obec: Annotated[str | None, Field(description="Count only payments published under this obec (or okres for anonymised recipients); Bratislava also counts Bratislava-Ružinov.")] = None,
    year: Annotated[int | None, Field(description="Financial year; default the newest indexed.")] = None,
    kind: Annotated[Kind | None, Field(description="natural_person: published with a surname; entity: a name only (companies, public bodies, also sole traders under their own name); anonymised: EUR 1,250 or less, a numeric code.")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    page: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """The largest CAP beneficiaries paid by PPA in a financial year, ranked by the amount under a
    measure and/or in an obec (or overall), with the number of beneficiaries and the sum over all of
    them. Amounts are EUR, EU funds plus national co-financing; recoveries are negative."""
    idx = index()
    year = _year(idx, year) or max(idx.years())
    code = None
    if measure:
        code = measure_code(measure.strip().upper())
        if not code or (code not in MEASURES and "." in code and not idx.db.execute(
                "SELECT 1 FROM amounts WHERE code = ? LIMIT 1", (code,)).fetchone()):
            raise ValueError(f"Unknown measure code {measure}; codes look like I.1, V.2 or VI.4.")
    rows, stats = idx.top(year, code=code, place=obec, kind=kind, limit=limit, offset=page * limit)
    ranking = []
    for n, b in enumerate(rows, page * limit + 1):
        item = {"rank": n, "name": b["name"], "kind": b["kind"], "places": b["places"], **_money(b["filtered"])}
        if (code or obec) and b["filtered"]["total"] != b["total"]:
            item["total_all_measures_and_places"] = b["total"]
        if b.get("parent"):
            item["parent_company"] = b["parent"]
        ranking.append(item)
    out = {
        "fiscal_year": year,
        "measure": {"code": code, "name": MEASURES.get(code)} if code else None,
        "obec": obec,
        "kind": kind,
        "beneficiaries": stats["beneficiaries"],
        "sum_total": stats["total"] or 0.0,
        "ranking": ranking,
        "has_more": stats["beneficiaries"] > (page + 1) * limit,
        "match_note": "PPA publishes no IČO; look a company up by name in rpo_search_organization (a matching name is not proof of the same entity).",
    }
    out.update(_notes(idx))
    return {k: v for k, v in out.items() if v is not None}
