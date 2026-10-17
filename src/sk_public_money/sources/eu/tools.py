"""Tools for EU funds 2021-2027 in Slovakia, from ITMS21+: projects, applications, payment claims,
the procurements of projects with their contracts and suppliers, and invoices.

Searches read the local index (sync.py); eu_get_project reads the project's detail and invoices
live and adds the procurements and claims from the index.
"""

from __future__ import annotations

import re
import time
from typing import Annotated, Literal

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from ...localdb import sync_command
from ...http import SourceUnavailable
from . import parse
from .client import ITMSClient
from .index import EUIndex, default_path

PAGE_SIZE = 20
STALE_AFTER = 8 * 86400
MAX_LIVE_LOOKUPS = 10
NO_INDEX = (f"The ITMS21+ index has not been built. Build it with {sync_command('eu')} (a few minutes from the "
            "prebuilt index; from the source, lists in a few minutes and procurement details in about 5 hours).")
NOT_COVERED = ("ITMS21+ covers the 2021-2027 programmes only. Projects of 2014-2020 (ITMS2014+) and the Recovery "
               "Plan (Plán obnovy) are not in this source.")
CODE = re.compile(r"^\s*([A-Z0-9]{6,12})\s*$", re.I)

_client: ITMSClient | None = None
_index: EUIndex | None = None


def client() -> ITMSClient:
    global _client
    if _client is None:
        _client = ITMSClient()
    return _client


def index() -> EUIndex:
    global _index
    if _index is None:
        if not default_path().exists():
            raise ValueError(NO_INDEX)
        _index = EUIndex()
    if not _index.get_meta("lists_synced_at"):
        raise ValueError(NO_INDEX)
    return _index


def _ico(value: str) -> str:
    ico = re.sub(r"\s+", "", value)
    if not ico.isdigit() or len(ico) > 8:
        raise ValueError(f"An IČO has up to 8 digits, got: {value}")
    return ico.zfill(8)


def _status(ix: EUIndex) -> dict:
    out = {"index": ix.coverage()}
    synced = ix.get_meta("lists_synced_at")
    if synced and time.time() - float(synced) > STALE_AFTER:
        out["index_note"] = f"The index is more than a week old; refresh it with {sync_command('eu')}."
    return out


def _item_row(ix: EUIndex, r: dict, claims: dict, roles: dict | None) -> dict:
    kind = r.pop("kind")
    recipient = r.pop("recipient_id", None) if kind == "project" else r.pop("applicant_id", None)
    partners = r.pop("partner_ids", [])
    out = {"kind": kind, "id": r.pop("id"), "code": r.pop("code", None), "name": r.pop("name", None), "status": r.pop("status", None)}
    out["programme"] = ix.programme(r.pop("programme_id", None))
    out["recipient" if kind == "project" else "applicant"] = ix.short_org(recipient)
    if partners:
        out["partners"] = [ix.short_org(p) for p in partners]
    if roles is not None:
        out["matched_as"] = sorted(roles.get((kind, out["id"]), []))
    r.pop("call_id", None)
    if len(r.get("places", [])) > 3:  # a nationwide project lists every region
        r["places"] = r["places"][:3] + [f"and {len(r['places']) - 3} more"]
    out.update(r)
    if kind == "project":
        out["payment_claims"] = claims.get(out["id"], {"count": 0})
    return out


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def eu_search_projects(
    ico: Annotated[str | None, Field(description="IČO of a recipient (prijímateľ, for applications the applicant) or partner.")] = None,
    role: Annotated[Literal["any", "recipient", "partner"], Field(description="With ico: the organisation's role.")] = "any",
    text: Annotated[str | None, Field(description="Words in the project's code, name or its organisations' names; ignores case and diacritics.")] = None,
    programme: Annotated[str | None, Field(description="Programme: PSK (Program Slovensko), PRH (fisheries), SKAT or SKCZ (Interreg Slovakia-Austria, -Czechia), AMIF, ISF, BMVI.")] = None,
    kind: Annotated[Literal["projects", "applications", "all"], Field(description="projects: contracted projects. applications: grant applications (ŽoNFP), approved or not. all: projects plus applications that did not become one.")] = "projects",
    sort: Annotated[Literal["amount", "newest"], Field(description="amount: largest contracted (or approved, else requested) amount first.")] = "amount",
    page: Annotated[int, Field(ge=0, description="0-based page; 20 per page.")] = 0,
) -> dict:
    """Search EU-funded projects of 2021-2027 (ITMS21+) by IČO, words and programme: recipient and
    partners with IČO, status, contracted total, dates, and the payment claims (ŽoP) filed so far
    with the amounts claimed. Use eu_get_project for amounts paid out, suppliers, procurements and
    invoices of one project."""
    ix = index()
    subject_ids = None
    if ico:
        subject_ids = ix.subject_ids(_ico(ico))
        if not subject_ids:
            return {"total": 0, "results": [], "note": f"No organisation with IČO {_ico(ico)} in ITMS21+. {NOT_COVERED}", **_status(ix)}
    programme_id = ix.find_programme(programme) if programme else None
    rows, total = ix.search_items(subject_ids=subject_ids, role=role, text=text, programme_id=programme_id,
                                  kind=kind, sort=sort, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    claims = ix.claims_summary([r["id"] for r in rows if r["kind"] == "project"])
    roles = ix.roles_of([(r["kind"], r["id"]) for r in rows], subject_ids) if subject_ids else None
    out = {
        "total": total,
        "page": page,
        "results": [_item_row(ix, r, claims, roles) for r in rows],
        "has_more": (page + 1) * PAGE_SIZE < total,
        **_status(ix),
    }
    if not total:
        out["note"] = NOT_COVERED
    return out


async def _org(ix: EUIndex | None, *, subject_id: int | None = None, supplier_id: int | None = None, budget: list[int]) -> dict | None:
    """An organisation from the index, or fetched live (at most budget[0] lookups per call)."""
    if subject_id is None and supplier_id is None:
        return None
    if ix is not None:
        o = ix.short_org(subject_id, supplier_id)
        if o and "note" not in o:
            return o
    if budget[0] <= 0:
        return {"subject_id": subject_id} if subject_id is not None else {"supplier_id": supplier_id}
    budget[0] -= 1
    try:
        raw = await client().get_json(f"subjekt/id/{subject_id}") if subject_id is not None else await client().supplier(supplier_id)
    except LookupError:
        return {"subject_id": subject_id} if subject_id is not None else {"supplier_id": supplier_id, "note": "unknown to the API"}
    org = parse.organisation(raw)
    if ix is not None and supplier_id is not None:
        ix.add_supplier(supplier_id, org)
    return {k: v for k, v in org.items() if k in ("name", "ico", "other_id")}


async def _invoices(ix: EUIndex | None, project_id: int, budget: list[int]) -> dict:
    rows = [parse.invoice_row(r) for r in await client().project_invoices(project_id)]
    if not rows:
        return {"count": 0}
    external = [r for r in rows if r.get("type") != "INTERNY"]
    groups: dict[tuple, dict] = {}
    for r in external:
        key = ("s", r["supplier_id"]) if r.get("supplier_id") is not None else ("u", r.get("supplier_subject_id"))
        g = groups.setdefault(key, {"invoices": 0, "total": 0.0})
        g["invoices"] += 1
        g["total"] = round(g["total"] + (r.get("amount") or 0), 2)
    by_supplier = []
    for (space, oid), g in sorted(groups.items(), key=lambda kv: -kv[1]["total"]):
        org = await _org(ix, supplier_id=oid, budget=budget) if space == "s" else await _org(ix, subject_id=oid, budget=budget)
        by_supplier.append({"supplier": org, **g})
    return {
        "count": len(rows),
        "total": round(sum(r.get("amount") or 0 for r in rows), 2),
        "internal_documents": {"count": len(rows) - len(external), "total": round(sum(r.get("amount") or 0 for r in rows if r.get("type") == "INTERNY"), 2)},
        "by_supplier": by_supplier,
    }


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def eu_get_project(
    project: Annotated[str, Field(description="Project ID or code (kód projektu, e.g. 401501F545) from eu_search_projects.")],
) -> dict:
    """One EU-funded project (ITMS21+) in full: recipient, partners and provider with IČO, programme
    and call, amounts contracted, paid to the recipient and by funding source, the funding contract
    (CRZ link), dates, description, indicators, amendments, the project's suppliers, its invoices
    summed by supplier, its procurements with contracts and main suppliers, and its payment claims."""
    value = project.strip()
    if not (value.isdigit() or CODE.match(value)):
        raise ValueError(f"Not a project ID or code: {project}")
    try:
        ix = index()
    except ValueError:
        ix = None
    raw = await client().project(int(value) if value.isdigit() else value.upper())
    detail = parse.project_detail(raw)
    budget = [MAX_LIVE_LOOKUPS]
    out = {k: detail.pop(k) for k in ("id", "code", "name", "acronym", "status") if k in detail}
    programme_id = detail.pop("programme_id", None)
    call_id = detail.pop("call_id", None)
    if ix is not None:
        out["programme"] = ix.programme(programme_id)
        out["call"] = ix.call(call_id)
    out["recipient"] = await _org(ix, subject_id=detail.pop("recipient_id", None), budget=budget)
    out["partners"] = [await _org(ix, subject_id=p, budget=budget) for p in detail.pop("partner_ids", [])]
    out["provider"] = await _org(ix, subject_id=detail.pop("provider_id", None), budget=budget)
    out.update(detail)
    try:
        out["invoices"] = await _invoices(ix, out["id"], budget)
    except SourceUnavailable as exc:
        out["invoices"] = {"note": f"Could not read the invoices: {exc}"}
    if ix is None:
        out["note"] = "Procurements and payment claims come from the local index, which has not been built: " + NO_INDEX
        return {k: v for k, v in out.items() if v not in (None, [], {})}
    ids, _ = ix.search_procurements(text=None, project_id=out["id"], limit=100, offset=0)
    procurements = [ix.procurement(i) for i in ids]
    for p in procurements:
        p.pop("projects", None)
    out["procurements"] = procurements
    claims = ix.claims(out["id"])
    for c in claims:
        c.pop("project_id", None)
        for key in ("recipient_id", "submitted_for_id", "lead_partner_id"):
            if key in c:
                c[key.removesuffix("_id")] = ix.short_org(c.pop(key))
    out["payment_claims"] = {**ix.claims_summary([out["id"]]).get(out["id"], {"count": 0}), "claims": claims}
    out.update(_status(ix))
    return {k: v for k, v in out.items() if v not in (None, [], {})}


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def eu_search_procurements(
    ico: Annotated[str | None, Field(description="IČO of a buyer (obstarávateľ) or a contract's main supplier.")] = None,
    role: Annotated[Literal["any", "buyer", "supplier"], Field(description="With ico: the organisation's role.")] = "any",
    text: Annotated[str | None, Field(description="Words in the procurement's code, name, buyer or supplier names; ignores case and diacritics.")] = None,
    project: Annotated[str | None, Field(description="Only procurements of this project (ID or code).")] = None,
    page: Annotated[int, Field(ge=0, description="0-based page; 20 per page.")] = 0,
) -> dict:
    """Search procurements that recipients of EU funds 2021-2027 ran for their projects (ITMS21+),
    newest first: buyer with IČO, status, estimated value, procedure, CPV, tenders received, Vestník
    reference, linked projects, and the signed contracts with number, amount and main supplier (IČO).
    With an IČO as supplier, also sums the invoices that supplier billed to EU projects, by recipient."""
    ix = index()
    kwargs: dict = {}
    icos = None
    if ico:
        icos = _ico(ico)
        subjects, suppliers = ix.subject_ids(icos), ix.supplier_ids(icos)
        if role in ("any", "buyer"):
            kwargs.update(buyer_subjects=subjects, buyer_suppliers=suppliers)
        if role in ("any", "supplier"):
            kwargs.update(supplier_ids=suppliers, supplier_subjects=subjects)
    project_id = None
    if project:
        value = project.strip()
        project_id = int(value) if value.isdigit() else ix.project_id_by_code(value.upper())
        if project_id is None:
            raise LookupError(f"No project {project} in the ITMS21+ index.")
    ids, total = ix.search_procurements(text=text, project_id=project_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE, **kwargs)
    out = {"total": total, "page": page, "results": [ix.procurement(i) for i in ids], "has_more": (page + 1) * PAGE_SIZE < total}
    if icos and role in ("any", "supplier") and page == 0:
        inv = ix.invoices_by_supplier(kwargs.get("supplier_ids") or [], kwargs.get("supplier_subjects") or [])
        if inv:
            out["invoices_as_supplier"] = inv
    out.update(_status(ix))
    partial = "procurement_details" in (out.get("index") or {})
    if any("details" in r for r in out["results"]) or (partial and (text or (icos and role != "buyer"))):
        out["details_note"] = (f"Some procurements have no details yet, so a supplier search can miss them; the index fills "
                               f"them in with {sync_command('eu')}.")
    if icos and not total and "invoices_as_supplier" not in out:
        out["note"] = f"No procurement or invoice with IČO {icos} in ITMS21+. Suppliers are matched by the main supplier of each contract only. {NOT_COVERED}"
    return out
