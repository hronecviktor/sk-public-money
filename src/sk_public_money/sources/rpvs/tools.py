"""Tools for the Register of Public Sector Partners (Register partnerov verejného sektora, rpvs.gov.sk)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from . import parse
from .client import RPVSClient, odata_str

PARTNER_EXPAND = "KonecniUzivateliaVyhod,OpravneneOsoby,VerejniFunkcionari,Vymaz,Pokuta,KvalifikovanePodnety,OverenieIdentifikacieKUV"
PERSON_SETS = {
    "beneficial_owner": ("KonecniUzivateliaVyhod", parse.beneficial_owner),
    "public_official": ("VerejniFunkcionari", parse.public_official),
    "authorized_person": ("OpravneneOsoby", parse.authorized_person),
}

_client: RPVSClient | None = None


def client() -> RPVSClient:
    global _client
    if _client is None:
        _client = RPVSClient()
    return _client


def _ico_filter(ico: str) -> str:
    ico = ico.replace(" ", "")
    # Slovak IČOs are stored zero-padded to 8 digits; foreign IDs are stored as given.
    variants = {ico, ico.zfill(8)} if ico.isdigit() else {ico}
    return " or ".join(f"Ico eq {odata_str(v)}" for v in sorted(variants))


async def _partner_identities(partner_ids: list[int]) -> dict[int, list[dict]]:
    """All identity records (names, IČO, address over time) of the given partners."""
    out: dict[int, list[dict]] = {pid: [] for pid in partner_ids}
    for i in range(0, len(partner_ids), 40):
        chunk = partner_ids[i : i + 40]
        rows, _ = await client().get_rows(
            "PartneriVerejnehoSektora",
            {"$filter": f"Partner/Id in ({','.join(map(str, chunk))})", "$expand": "Partner,Adresa,PravnaForma"},
            max_rows=1000,
        )
        for row in rows:
            out.setdefault(row["Partner"]["Id"], []).append(row)
    return out


def _partner_summary(partner_id: int, identities: list[dict]) -> dict:
    current = parse.latest(identities)
    return {
        "partner_id": partner_id,
        "url": parse.web_url(current["Partner"].get("CisloVlozky")),
        "registered": current.get("PlatnostDo") is None,
        **{k: v for k, v in parse.partner_identity(current).items() if k in ("name", "ico", "form", "address")},
    }


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def rpvs_get_partner(
    ico: Annotated[str | None, Field(description="IČO of the company or sole trader (8 digits; shorter Slovak IČOs are zero-padded automatically).")] = None,
    partner_id: Annotated[int | None, Field(description="RPVS partner ID, as returned by rpvs_search_partner or rpvs_search_person.")] = None,
    include_history: Annotated[bool, Field(description="Also list former beneficial owners, authorized persons and officials, and past names/addresses.")] = False,
) -> dict:
    """A public-sector partner in RPVS: its name, IČO and address, its current beneficial owners
    (konečný užívateľ výhod, KUV, with birth year and whether they are public officials), its authorized
    person (oprávnená osoba, usually a law firm that verified the owners), any deletion, fine or qualified
    complaint. Companies that were deleted and registered again have several partner records; all are returned."""
    if (ico is None) == (partner_id is None):
        raise ValueError("Give exactly one of ico or partner_id.")

    if ico is not None:
        rows, _ = await client().get_rows(
            "PartneriVerejnehoSektora", {"$filter": _ico_filter(ico), "$expand": "Partner"}, max_rows=200
        )
        partner_ids = sorted({r["Partner"]["Id"] for r in rows})
        if not partner_ids:
            raise LookupError(f"No partner with IČO {ico} in RPVS. It may never have registered (only partners receiving public money above the legal thresholds must).")
    else:
        partner_ids = [partner_id]

    identities = await _partner_identities(partner_ids)
    partners = []
    for pid in partner_ids:
        entity = await client().get_json(f"Partneri({pid})", {"$expand": PARTNER_EXPAND})
        partners.append(parse.partner(identities.get(pid, []), entity, include_history=include_history))
    # Registered partner first, then the most recently valid.
    partners.sort(key=lambda p: (not p["registered"], p.get("valid_to") is not None, -(p["partner_id"])))
    return {"partners": partners}


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def rpvs_search_partner(
    name: Annotated[str, Field(min_length=3, description="Part of the business name (or a sole trader's surname). Case-insensitive; diacritics must match.")],
    include_deleted: Annotated[bool, Field(description="Also return partners that were deleted from the register or renamed.")] = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict:
    """Find public-sector partners by name. Returns partner IDs, names, IČO and addresses; use
    rpvs_get_partner for beneficial owners."""
    needle = odata_str(name.lower())
    flt = f"(contains(tolower(ObchodneMeno),{needle}) or contains(tolower(Priezvisko),{needle}))"
    if not include_deleted:
        flt += " and PlatnostDo eq null"
    rows, total = await client().get_rows("PartneriVerejnehoSektora", {"$filter": flt, "$expand": "Partner"}, max_rows=limit * 3)
    partner_ids = list(dict.fromkeys(r["Partner"]["Id"] for r in rows))[:limit]
    identities = await _partner_identities(partner_ids)
    return {
        "matching_records": total,
        "partners": [_partner_summary(pid, identities[pid]) for pid in partner_ids if identities.get(pid)],
        "has_more": (total or 0) > len(rows),
    }


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def rpvs_search_person(
    surname: Annotated[str, Field(min_length=2, description="Exact surname. Case-insensitive; diacritics must match (Šimečková, not Simeckova).")],
    first_name: Annotated[str | None, Field(description="First name or its beginning.")] = None,
    role: Annotated[Literal["any", "beneficial_owner", "public_official", "authorized_person"], Field(description="Which RPVS roles to search.")] = "any",
    include_former: Annotated[bool, Field(description="Also return roles that have ended.")] = False,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> dict:
    """Find where a person appears in RPVS: as beneficial owner (KUV) of a partner, as a public
    official (verejný funkcionár) of a partner, or as an authorized person. Each hit names the partner
    with its IČO, which can be looked up in CRZ. A matching name is not proof of the same person;
    compare birth years."""
    flt = f"tolower(Priezvisko) eq {odata_str(surname.strip().lower())}"
    if first_name:
        flt += f" and startswith(tolower(Meno),{odata_str(first_name.strip().lower())})"
    if not include_former:
        flt += " and PlatnostDo eq null"

    roles = list(PERSON_SETS) if role == "any" else [role]
    hits: list[tuple[str, dict, int]] = []
    totals: dict[str, int | None] = {}
    for r in roles:
        entity_set, normalize = PERSON_SETS[r]
        rows, totals[r] = await client().get_rows(entity_set, {"$filter": flt, "$expand": "Partner"}, max_rows=limit)
        hits.extend((r, normalize(row), row["Partner"]["Id"]) for row in rows)
    hits = hits[:limit]

    identities = await _partner_identities(sorted({pid for _, _, pid in hits}))
    results = []
    for r, person, pid in hits:
        results.append({"role": r, **person, "partner": _partner_summary(pid, identities[pid]) if identities.get(pid) else {"partner_id": pid}})
    return {
        "matching": {r: totals[r] for r in roles},
        "results": results,
        "has_more": sum(t or 0 for t in totals.values()) > len(results),
    }
