"""Tools for the Register of Public Sector Partners (Register partnerov verejného sektora, rpvs.gov.sk)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from . import parse
from ...text import fold
from .client import MAX_LETTERS, QueryTooComplex, RPVSClient, odata_str, spelled_like

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
        rows, _, _ = await client().get_rows(
            "PartneriVerejnehoSektora",
            {"$filter": f"Partner/Id in ({','.join(map(str, chunk))})", "$expand": "Partner,Adresa,PravnaForma"},
            max_rows=1000,
            max_pages=100,
        )
        for row in rows:
            out.setdefault(row["Partner"]["Id"], []).append(row)
    return out


async def _rows_within_node_limit(entity_set: str, build_filter, keep, *, letters: int = MAX_LETTERS, **kwargs):
    """Query with build_filter(letters), retrying with fewer letters while RPVS finds it too complex.

    Returns (letters used, rows, server count, more). keep must verify locally what the filter leaves
    unchecked, so fewer letters only cost precision of the count, never correctness of the rows.
    """
    while True:
        try:
            rows, total, more = await client().get_rows(
                entity_set, {"$filter": build_filter(letters), "$expand": "Partner"}, keep=keep, **kwargs
            )
            return letters, rows, total, more
        except QueryTooComplex:
            if letters <= 2:
                raise ValueError("RPVS rejected the search as too complex; try a shorter name.") from None
            letters = max(2, letters - 3)


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
    (konečný užívateľ výhod, KUV, with birth date and whether they are public officials), its authorized
    person (oprávnená osoba, usually a law firm that verified the owners), any deletion, fine or qualified
    complaint. Companies that were deleted and registered again have several partner records; all are returned."""
    if (ico is None) == (partner_id is None):
        raise ValueError("Give exactly one of ico or partner_id.")

    if ico is not None:
        rows, _, _ = await client().get_rows(
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
    name: Annotated[str, Field(min_length=3, description="Start of the business name or a sole trader's surname, matched with any case and diacritics (siroky finds ŠIROKÝ s.r.o.). Also matched anywhere in the name, but there only as typed or without diacritics.")],
    include_deleted: Annotated[bool, Field(description="Also return partners that were deleted from the register or renamed.")] = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict:
    """Find public-sector partners by name. Returns partner IDs, names, IČO and addresses; use
    rpvs_get_partner for beneficial owners."""
    # The API escapes LIKE wildcards, so diacritics can only be matched position by position, which
    # works for the start of a name. Substring matches cover the spelling as typed and without accents.
    # Prefixes longer than the node budget allows are checked only partly on the server; keep() finishes.
    name = name.strip()
    wanted = fold(name)
    needles = sorted({odata_str(name.lower()), odata_str(fold(name))})

    def build_filter(letters: int) -> str:
        per_field = max(1, letters // 2)
        alternatives = [
            spelled_like("ObchodneMeno", name, prefix=True, max_letters=per_field),
            spelled_like("Priezvisko", name, prefix=True, max_letters=per_field),
        ]
        alternatives += [f"contains(tolower(ObchodneMeno),{n})" for n in needles]
        flt = "(" + " or ".join(alternatives) + ")"
        return flt if include_deleted else flt + " and PlatnostDo eq null"

    def keep(row: dict) -> bool:
        business, surname = fold((row.get("ObchodneMeno") or "").strip()), fold((row.get("Priezvisko") or "").strip())
        return business.startswith(wanted) or surname.startswith(wanted) or wanted in business

    _, rows, _, more = await _rows_within_node_limit("PartneriVerejnehoSektora", build_filter, keep, max_rows=limit * 3)
    partner_ids = list(dict.fromkeys(r["Partner"]["Id"] for r in rows))[:limit]
    identities = await _partner_identities(partner_ids)
    return {
        "partners": [_partner_summary(pid, identities[pid]) for pid in partner_ids if identities.get(pid)],
        "has_more": more or len(dict.fromkeys(r["Partner"]["Id"] for r in rows)) > limit,
    }


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def rpvs_search_person(
    surname: Annotated[str, Field(min_length=2, description="Exact surname, matched with any case and diacritics (Siroky finds Široký, Siroký, Široky...).")],
    first_name: Annotated[str | None, Field(description="First name or its beginning, matched with any case and diacritics.")] = None,
    role: Annotated[Literal["any", "beneficial_owner", "public_official", "authorized_person"], Field(description="Which RPVS roles to search.")] = "any",
    include_former: Annotated[bool, Field(description="Also return roles that have ended.")] = False,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> dict:
    """Find where a person appears in RPVS: as beneficial owner (KUV) of a partner, as a public
    official (verejný funkcionár) of a partner, or as an authorized person. Each hit names the partner
    with its IČO, which can be looked up in CRZ. A matching name is not proof of the same person;
    compare birth dates."""
    surname, first_name = surname.strip(), (first_name or "").strip()
    wanted_surname, wanted_first = fold(surname), fold(first_name)

    def build_filter(letters: int) -> str:
        # Spend the letter budget on the surname first; the rest is verified by keep().
        surname_letters = min(len(surname), max(1, letters - min(len(first_name), 2)))
        first_letters = min(len(first_name), letters - surname_letters)
        flt = spelled_like("Priezvisko", surname, max_letters=surname_letters)
        if first_letters:
            flt += " and " + spelled_like("Meno", first_name, prefix=True, max_letters=first_letters)
        return flt if include_former else flt + " and PlatnostDo eq null"

    def keep(row: dict) -> bool:
        return fold((row.get("Priezvisko") or "").strip()) == wanted_surname and fold(
            (row.get("Meno") or "").strip()
        ).startswith(wanted_first)

    roles = list(PERSON_SETS) if role == "any" else [role]
    hits: list[tuple[str, dict, int]] = []
    totals: dict[str, int | None] = {}
    more = False
    letters = MAX_LETTERS
    for r in roles:
        entity_set, normalize = PERSON_SETS[r]
        letters, rows, total, role_more = await _rows_within_node_limit(
            entity_set, build_filter, keep, letters=letters, max_rows=limit
        )
        # The server's count is exact only when it checked every letter.
        totals[r] = total if letters >= len(surname) + len(first_name) else None
        more = more or role_more
        hits.extend((r, normalize(row), row["Partner"]["Id"]) for row in rows)
    more = more or len(hits) > limit
    hits = hits[:limit]

    identities = await _partner_identities(sorted({pid for _, _, pid in hits}))
    results = []
    for r, person, pid in hits:
        results.append({"role": r, **person, "partner": _partner_summary(pid, identities[pid]) if identities.get(pid) else {"partner_id": pid}})
    return {
        # null when the name is too long for the server to count exact matches
        "matching": {r: totals[r] for r in roles},
        "results": results,
        "has_more": more,
    }
