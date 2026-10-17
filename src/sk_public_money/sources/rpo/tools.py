"""Tools for the Register of Legal Entities (Register právnických osôb, RPO)."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from ...text import fold
from ..court import disqualified
from . import parse
from .client import MAX_RESULTS, RPOClient

MAX_SAME_ICO = 5
PERSON_PARAMS = {
    "statutory_body": ("statutoryBodies", "statutoryBodyFamilyName", "statutoryBodyGivenName"),
    "stakeholder": ("stakeholders", "stakeholderPersonFamilyName", "stakeholderPersonGivenName"),
}

_client: RPOClient | None = None


def client() -> RPOClient:
    global _client
    if _client is None:
        _client = RPOClient()
    return _client


def normalize_ico(ico: str) -> str:
    ico = ico.replace(" ", "")
    if not ico.isdigit() or len(ico) > 8:
        raise ValueError(f"An IČO has up to 8 digits, got: {ico}")
    return ico.zfill(8)  # RPO compares exactly and stores Slovak IČOs zero-padded


def _active_first(results: list[dict]) -> list[dict]:
    return sorted(results, key=lambda r: r.get("termination") is not None)


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def rpo_get_organization(
    ico: Annotated[str | None, Field(description="IČO (up to 8 digits; shorter ones are zero-padded).")] = None,
    rpo_id: Annotated[int | None, Field(description="RPO record ID, as returned by the rpo_search_* tools.")] = None,
    include_history: Annotated[bool, Field(description="Also list former statutory bodies, stakeholders, capital, addresses and other legal facts, in separate former_* lists (newest end first).")] = False,
) -> dict:
    """A company, sole trader, public body or other legal entity from RPO, the official register of
    all of them: name and former names, IČO, seat, legal form and status, founding and dissolution
    dates, source register (e.g. Obchodný register with its court and file number), statutory body
    (directors, board), stakeholders (partners, sole shareholder, supervisory board, procurists),
    registered capital and contributions, predecessors and successors. RPO publishes no birth dates
    and no beneficial owners; see rpvs_get_partner for owners. Current directors and procurists whose
    name is in the register of disqualified persons carry a 'disqualified' flag (see court_disqualified)."""
    if (ico is None) == (rpo_id is None):
        raise ValueError("Give exactly one of ico or rpo_id.")
    if ico is not None:
        found = _active_first(await client().search(identifier=normalize_ico(ico)))
        if not found:
            raise LookupError(f"No organization with IČO {normalize_ico(ico)} in RPO.")
        ids = [r["id"] for r in found[:MAX_SAME_ICO]]
    else:
        ids = [rpo_id]
    orgs = [parse.organization(await client().entity(i), include_history=include_history) for i in ids]
    out: dict = {"organizations": orgs}
    if ico is not None and len(found) > MAX_SAME_ICO:
        out["not_shown"] = len(found) - MAX_SAME_ICO
    # Flags current directors and procurists found in the register of disqualified persons.
    note = await disqualified.flag([p for org in orgs for p in disqualified.officers(org)])
    if note:
        out["disqualification_check"] = note
    return out


# Legal-form suffixes as name_key leaves them (folded, punctuation as spaces), longest first.
LEGAL_FORMS = sorted(
    ("a s", "s r o", "spol s r o", "k s", "v o s", "s e", "s p", "n o"),
    key=len, reverse=True,
)
EXACT, PREFIX, CONTAINS, FORMER_NAME, NO_MATCH = range(5)


def name_key(text: str) -> str:
    """Folded name without punctuation and legal-form suffix: 'Železiarne Podbrezová a.s.' -> 'zeleziarne podbrezova'."""
    key = " ".join(re.sub(r"[^\w]+", " ", fold(text)).split())
    for form in LEGAL_FORMS:
        if key.endswith(" " + form):
            return key[: -len(form) - 1]
    return key


def _relevance(key: str, wanted: str) -> int:
    if key == wanted:
        return EXACT
    if key.startswith(wanted):
        return PREFIX
    return CONTAINS if wanted in key else NO_MATCH


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def rpo_search_organization(
    name: Annotated[str, Field(min_length=3, description="Word(s) of the name, current or former, matched with any case and diacritics (siroky finds ŠIROKÝ s.r.o.).")],
    municipality: Annotated[str | None, Field(description="Town of the seat, matched the same way.")] = None,
    include_terminated: Annotated[bool, Field(description="Also return dissolved organizations.")] = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict:
    """Find organizations in RPO by name: companies, sole traders, public bodies, associations.
    Returns RPO IDs, IČO, current name, seat and dates; use rpo_get_organization for people and capital.
    Searches can take 5-30 s."""
    results = await client().search(
        fullName=name.strip(),
        addressMunicipality=municipality.strip() if municipality else None,
        onlyActive=None if include_terminated else "true",
    )
    wanted = name_key(name)
    ranked = []
    for rec in results:
        hit = parse.summary(rec)
        rank = _relevance(name_key(hit["name"] or ""), wanted)
        if rank == NO_MATCH:
            former = [n["value"] for n in rec.get("fullNames") or [] if wanted in name_key(n.get("value") or "")]
            if former:
                hit["matched_former_name"] = former[-1]
                rank = FORMER_NAME
        ranked.append(((hit["terminated"] is not None, rank, fold(hit["name"] or "")), hit))
    # Active first; then the name itself, names starting with the search, containing it, former names.
    hits = [hit for _, hit in sorted(ranked, key=lambda pair: pair[0])]
    return {
        "matching": len(results),
        # RPO returns at most 500 records; narrow the search when this is true.
        "capped": len(results) >= MAX_RESULTS,
        "organizations": hits[:limit],
        "has_more": len(hits) > limit,
    }


def _matches(person: dict | None, surname: str, first_name: str) -> bool:
    if not person:
        return False
    family = [fold(n.strip()) for n in person.get("familyNames") or []]
    given = [fold(n.strip()) for n in person.get("givenNames") or []]
    surname_ok = surname in family or surname == " ".join(family)
    return surname_ok and (not first_name or any(g.startswith(first_name) for g in given))


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def rpo_search_person(
    surname: Annotated[str, Field(min_length=2, description="Exact surname, matched with any case and diacritics (Siroky finds Široký).")],
    first_name: Annotated[str | None, Field(description="First name or its beginning, matched the same way.")] = None,
    role: Annotated[Literal["any", "statutory_body", "stakeholder"], Field(description="statutory_body = director, board member, sole trader; stakeholder = partner, shareholder, supervisory board, procurist.")] = "any",
    include_former: Annotated[bool, Field(description="Also return roles the person no longer holds.")] = False,
    include_terminated: Annotated[bool, Field(description="Also search dissolved organizations.")] = False,
    limit: Annotated[int, Field(ge=1, le=25, description="Organizations to check in detail (each takes about a second).")] = 10,
) -> dict:
    """Find organizations where a person is in the statutory body (konateľ, predstavenstvo, sole
    trader) or a stakeholder (spoločník, akcionár, dozorná rada, prokurista), with each
    organization's IČO. RPO has no birth dates, so a matching name is not proof of the same person;
    compare addresses, or birth dates in RPVS. Roles of a person whose name is in the register of
    disqualified persons carry a 'disqualified' flag (see court_disqualified)."""
    surname, first_name = surname.strip(), (first_name or "").strip()
    wanted_surname, wanted_first = fold(surname), fold(first_name)
    roles = list(PERSON_PARAMS) if role == "any" else [role]

    candidates: dict[int, dict] = {}
    capped = False
    for r in roles:
        _, family_param, given_param = PERSON_PARAMS[r]
        # The server matches the names anywhere in the whole organization (possibly two different
        # people), so every candidate is verified on its full record below.
        found = await client().search(
            **{family_param: surname, given_param: first_name or None},
            onlyActive=None if include_terminated else "true",
        )
        capped = capped or len(found) >= MAX_RESULTS
        for rec in found:
            candidates.setdefault(rec["id"], rec)

    ordered = _active_first(list(candidates.values()))
    results, checked, former_only = [], 0, 0
    for rec in ordered:
        if len(results) >= limit:
            break
        entity = await client().entity(rec["id"])
        checked += 1
        org = parse.organization(entity)
        on = parse.last_day(entity, parse.today())
        named = [
            (r, p) for r in roles for p in entity.get(PERSON_PARAMS[r][0]) or []
            if _matches(p.get("personName"), wanted_surname, wanted_first)
        ]
        held = [{"kind": r, **parse.party(p)} for r, p in named if include_former or parse.is_current(p, on)]
        former_only += bool(named) and not held
        if held:
            summary = {k: org[k] for k in ("rpo_id", "ico", "name", "address", "legal_form", "established", "terminated")}
            results.append({"organization": summary, "roles": held})

    # Flags roles whose holder's name is in the register of disqualified persons.
    note = await disqualified.flag([r for res in results for r in res["roles"]])
    out = {
        # Organizations where the server found the name somewhere; not all are this person.
        "candidates": len(candidates),
        "capped": capped,
        "checked": checked,
        "results": results,
        # Checked organizations where the person held only roles that have ended (see include_former).
        "former_roles_only": former_only,
        "has_more": checked < len(ordered),
    }
    if note:
        out["disqualification_check"] = note
    return out
