"""Tools for the Register of Financial Statements (Register účtovných závierok, registeruz.sk)."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from . import parse
from .client import RUZClient

CODEBOOKS = ("pravne-formy", "velkosti-organizacie", "druhy-vlastnictva", "sk-nace")
MAX_STATEMENTS = 40

_client: RUZClient | None = None


def client() -> RUZClient:
    global _client
    if _client is None:
        _client = RUZClient()
    return _client


async def _codebooks() -> dict[str, dict[str, str]]:
    return {name: await client().codebook(name) for name in CODEBOOKS}


async def _entities(ico: str | None, ruz_id: int | None) -> list[dict]:
    """Live entity records for an IČO or RÚZ ID. RÚZ keeps deleted duplicates under the same IČO; they are skipped."""
    if (ico is None) == (ruz_id is None):
        raise ValueError("Give exactly one of ico or ruz_id.")
    if ruz_id is not None:
        records = [await client().entity(ruz_id)]
    else:
        ico = parse.ico(ico)
        records = [await client().entity(i) for i in await client().entity_ids(ico)]
    live = [r for r in records if r.get("stav") != parse.DELETED]
    if not live:
        raise LookupError(
            f"No accounting entity with {'IČO ' + ico if ico else f'RÚZ ID {ruz_id}'} in RÚZ."
            + (" Its only record was deleted." if records else "")
        )
    return live


async def _statements(entity: dict) -> tuple[list[dict], bool]:
    """The entity's statements, newest period first, and whether older ones were left out."""
    ids = sorted(entity.get("idUctovnychZavierok", []), reverse=True)
    records = [await client().statement(i) for i in ids[:MAX_STATEMENTS]]
    records = [r for r in records if r.get("stav") != parse.DELETED]
    records.sort(key=lambda r: (parse.period_end(r), r["id"]), reverse=True)
    return records, len(ids) > MAX_STATEMENTS


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def ruz_search_entity(
    query: Annotated[str, Field(min_length=3, description="Name or words of the name (any case and diacritics: 'zeleznice' finds Železnice; each word matches word starts), or an IČO or DIČ.")],
) -> dict:
    """Find accounting entities (companies, sole traders, non-profits, public bodies) in RÚZ by name.
    Returns at most 10 matches with IČO; use ruz_get_entity or ruz_get_financials next."""
    query = " ".join(query.split())
    hits = await client().suggest(query)
    results = [
        {
            "ruz_id": int(h["id"]),
            "name": re.sub(r"</?b>", "", h.get("entityName") or ""),
            "ico": h.get("entNumber") or None,
            "dic": h.get("taxNumber") or None,
        }
        for h in hits
    ]
    return {
        "results": results,
        # The portal's suggestion list stops at 10.
        "has_more": len(results) >= 10,
    }


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def ruz_get_entity(
    ico: Annotated[str | None, Field(description="IČO (up to 8 digits; spaces are ignored and short IČOs zero-padded).")] = None,
    ruz_id: Annotated[int | None, Field(description="RÚZ entity ID, as returned by ruz_search_entity.")] = None,
) -> dict:
    """An accounting entity in RÚZ (Register účtovných závierok): name, IČO, DIČ, address, legal form,
    SK NACE activity, ŠÚSR employee band and ownership, plus every financial statement (účtovná závierka)
    it filed: period, individual or consolidated, filing and approval dates. The first call for an entity
    with many statements can take 10-30 s; they are cached afterwards."""
    codebooks = await _codebooks()
    out = []
    for rec in await _entities(ico, ruz_id):
        statements, truncated = await _statements(rec)
        item = parse.entity(rec, codebooks)
        item["statements"] = [parse.statement(s) for s in statements]
        if truncated:
            item["statements_note"] = f"Only the {MAX_STATEMENTS} most recently filed statements are listed."
        item["annual_reports"] = len(rec.get("idVyrocnychSprav", []))
        out.append(item)
    return {"entities": out}


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def ruz_get_financials(
    ico: Annotated[str | None, Field(description="IČO (up to 8 digits; spaces are ignored and short IČOs zero-padded).")] = None,
    ruz_id: Annotated[int | None, Field(description="RÚZ entity ID, as returned by ruz_search_entity.")] = None,
    years: Annotated[list[int] | None, Field(description="Years the statement period ends in. Default: the latest statements.")] = None,
    consolidated: Annotated[bool, Field(description="Read consolidated (group) statements instead of individual ones.")] = False,
    limit: Annotated[int, Field(ge=1, le=15, description="Maximum statements when years is not given.")] = 3,
) -> dict:
    """Key figures per year from an entity's financial statements in RÚZ: revenue (tržby, sales),
    total_income (all výnosy/príjmy), net_profit (výsledok hospodárenia po zdanení), total_assets and
    equity, in EUR. Each figure names the report row and column it came from; figures that could not be
    read are listed under not_mapped with the reason. Companies reporting under IFRS (most large and
    listed ones) file only PDFs: their documents are linked instead. Employees come only as the ŠÚSR band
    on the entity. Amounts are as filed: whole euros or with cents depending on the filer."""
    codebooks = await _codebooks()
    out = []
    for rec in await _entities(ico, ruz_id):
        statements, _ = await _statements(rec)
        wanted = [s for s in statements if (parse.statement_kind(s) != "individual") == consolidated]
        if years:
            wanted = [s for s in wanted if parse.statement(s).get("year") in set(years)]
        else:
            wanted = wanted[:limit]
        results = []
        for st in wanted:
            pairs, documents = [], []
            for report_id in st.get("idUctovnychVykazov", []):
                report = await client().report(report_id)
                if report.get("stav") == parse.DELETED:
                    continue
                template = await client().template(report["idSablony"]) if report.get("idSablony") else {}
                documents.append(parse.document(report, template))
                if report.get("obsah", {}).get("tabulky"):
                    pairs.append((report, template))
            figures, not_mapped = parse.key_figures(pairs)
            result = {**parse.statement(st), "figures": figures}
            if not_mapped:
                result["not_mapped"] = not_mapped
            units = {r.get("mena") for r, _ in pairs if r.get("mena")}
            if units:
                result["unit"] = ", ".join(sorted(units))
            result["documents"] = documents
            results.append(result)
        entity = parse.entity(rec, codebooks)
        out.append({
            **{k: entity[k] for k in ("ruz_id", "url", "name", "ico", "employees") if k in entity},
            "statements": results,
            **({} if results else {"note": "No matching statements; ruz_get_entity lists all of them."}),
        })
    return {"entities": out}
