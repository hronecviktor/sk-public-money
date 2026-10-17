"""Tools for the Finančná správa SR lists: a taxpayer's VAT status, reliability index and tax
figures by IČO; tax debtors by name; and the VAT payers flagged for cancellation or struck off.

Everything comes from the local index built by sk-public-money-sync fs (see sync.py).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from ...localdb import sync_command
from . import parse, sync
from .client import FSClient
from .index import LISTS, FSIndex, default_path

PAGE_SIZE = 20
VAT_RETURNS = 12
NO_INDEX = (f"The Finančná správa index has not been built yet. Build it with {sync_command('fs')} "
            "(downloads about 0.7 GB in about 15 minutes; the index takes about 0.75 GB).")
LICENCE = "Zdroj údajov: Finančná správa SR - https://opendata.financnasprava.sk/ (CC BY 4.0)"
# What a DRUH_REG_DPH means (zákon o DPH 222/2004 Z. z.).
VAT_KINDS = {
    "§4": "VAT payer (platiteľ DPH), seat or business in Slovakia",
    "§4b": "VAT group registration (skupina DPH): the group files one VAT return for its members",
    "§5": "VAT payer, foreign person (zahraničná osoba)",
    "§7": "registered for intra-EU acquisitions of goods, not a VAT payer",
    "§7a": "registered for cross-border services, not a VAT payer",
}
DEBTOR_NOTE = ("The debtor list publishes no IČO: entries are matched by name only, which is no proof of the same "
               "taxpayer: compare the address. Debts are as of the list date.")

_index: FSIndex | None = None
_client: FSClient | None = None


def index() -> FSIndex:
    global _index
    if _index is None:
        if not default_path().exists():
            raise ValueError(NO_INDEX)
        _index = FSIndex()
    if not _index.is_built():
        raise ValueError(NO_INDEX)
    return _index


def client() -> FSClient:
    global _client
    if _client is None:
        _client = FSClient()
    return _client


def _stale(ix: FSIndex) -> dict:
    """A note naming lists older than twice their publication interval."""
    today = date.today()
    old = []
    for name, info in ix.lists().items():
        try:
            age = (today - date.fromisoformat(info["as_of"] or "")).days
        except ValueError:
            continue
        if age > 2 * LISTS[name][1] + 2:
            old.append(f"{name} ({info['as_of']})")
    missing = [n for n in LISTS if n not in ix.lists()]
    note = []
    if old:
        note.append(f"Lists older than usual: {', '.join(old)}; update with {sync_command('fs')}.")
    if missing:
        note.append(f"Lists not indexed: {', '.join(missing)}; build them with {sync_command('fs')}.")
    return {"index_note": " ".join(note)} if note else {}


def _identifier(value: str) -> tuple[str | None, str | None]:
    """(ico, dic) from an IČO (up to 8 digits, or a 12-digit unit IČO), a DIČ (10 digits) or an IČ DPH."""
    raw = value.replace(" ", "").upper()
    if raw.startswith("SK") and (dic := parse.dic_of(raw)):
        return None, dic
    if not raw.isdigit():
        raise ValueError(f"Expected an IČO (up to 8 digits), a DIČ (10 digits) or an IČ DPH (SK + 10 digits), got: {value}")
    if len(raw) <= 8:
        return raw.zfill(8), None
    if len(raw) == 10:
        return None, raw
    if len(raw) == 12:
        return raw, None
    raise ValueError(f"Expected an IČO (up to 8 digits), a DIČ (10 digits) or an IČ DPH (SK + 10 digits), got: {value}")


def _drop_empty(row: dict) -> dict:
    return {k: v for k, v in row.items() if v not in (None, "", [], {})}


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def fs_get_taxpayer(
    ico: Annotated[str, Field(description="IČO (up to 8 digits, spaces allowed); a DIČ (10 digits) or IČ DPH (SK + 10 digits) works too.")],
    vat_history: Annotated[bool, Field(description="Every VAT return row indexed (lists since June 2018) instead of the latest 12.")] = False,
) -> dict:
    """What the Slovak tax authority (Finančná správa) publishes about a taxpayer: VAT registration
    (kind and dates), index daňovej spoľahlivosti (vysoko spoľahlivý / spoľahlivý / menej
    spoľahlivý), presence on the lists of VAT payers with reasons to cancel their registration and of
    VAT payers struck off, corporate income tax assessed and tax loss per tax period, VAT returns'
    excess deduction (nadmerný odpočet) or own tax liability per period, and tax debtor list entries
    with the same name (matched by name only). Amounts in EUR."""
    want_ico, want_dic = _identifier(ico)
    ix = index()
    out: dict = {}
    out.update(await sync.refresh(ix, client()) or {})
    dics = [want_dic] if want_dic else ix.dics_for_ico(want_ico)
    subjects = [s for d in dics if (s := ix.subject(d))]
    if not want_ico and subjects:
        want_ico = subjects[0]["ico"]
    vat = ix.vat_registrations(want_ico, dics)
    flags = ix.vat_flags(want_ico, [v["ic_dph"] for v in vat] + [f"SK{d}" for d in dics])
    if not subjects and not vat and not flags:
        raise LookupError(
            f"{ico} is on none of the Finančná správa lists indexed (VAT register, reliability index, corporate tax, "
            "VAT flags). Natural persons without VAT registration and entities that file no returns are not listed."
        )
    many = len(dics) > 1
    names = [s["name"] for s in subjects if s.get("name")] + [f["name"] for f in flags if f.get("name")]
    out.update({
        "ico": want_ico,
        "taxpayers": [
            _drop_empty({"dic": s["dic"], "ico": s["ico"], "name": s["name"], "address": s["place"],
                         "reliability_index": s["rating"]})
            for s in subjects
        ],
        "vat_registration": [
            _drop_empty({
                "ic_dph": v["ic_dph"], "kind": v["kind"], "kind_means": VAT_KINDS.get(v["kind"] or ""),
                "registered": v["registered"], "vat_payer_from": v["valid_from"], "kind_changed": v["kind_changed"],
            })
            for v in vat
        ] or "not in the VAT register",
        "vat_cancellation_reasons": [
            _drop_empty({"ic_dph": f["ic_dph"], "name": f["name"], "address": f["place"], "violation_year": f["year"],
                         "published": f["published"]})
            for f in flags if f["list"] == "cancellation_reasons"
        ],
        "vat_struck_off": [
            _drop_empty({"ic_dph": f["ic_dph"], "name": f["name"], "address": f["place"], "violation_year": f["year"],
                         "published": f["published"], "struck_off": f["struck_off"]})
            for f in flags if f["list"] == "struck_off"
        ],
        "corporate_income_tax": [
            _drop_empty({
                **({"dic": r["dic"]} if many else {}),
                "period": f"{r['period_from']} – {r['period_to']}",
                "tax_assessed": parse.eur(r["tax"]), "additional_tax_assessed": parse.eur(r["additional"]) or None,
                "tax_loss": parse.eur(r["loss"]) or None, "list_of": r["release"],
            })
            for r in ix.cit(dics)
        ] if dics else [],
    })
    periods = [r["period"] for r in out["corporate_income_tax"]]
    if len(periods) != len(set(periods)):
        out["corporate_income_tax_note"] = ("A period listed more than once was published in several quarterly lists (list_of): "
                                            "a later row may carry only an additional assessment after a supplementary return. "
                                            "Amounts are as published in each list; do not add them up blindly.")
    returns = ix.vat_returns(dics) if dics else []
    shown = returns if vat_history else returns[:VAT_RETURNS]
    out["vat_returns"] = _vat_rows(shown, many)
    notes = []
    if len(returns) > len(shown):
        notes.append(f"{len(returns) - len(shown)} older rows not shown; vat_history=true lists them all.")
    if any("list_of" in r for r in out["vat_returns"]):
        notes.append("A period with list_of was published in several quarterly lists (e.g. after a corrective return); "
                     "amounts are as published in each.")
    if notes:
        out["vat_returns_note"] = " ".join(notes)
    groups = ix.vat_groups_led_by([s["name"] for s in subjects if s.get("name")])
    if groups:
        group_dics = [g["dic"] for g in groups]
        group_returns = ix.vat_returns(group_dics)
        out["vat_group_represented"] = {
            "matched_by": "name",
            "groups": [
                _drop_empty({"dic": g["dic"], "name": g["name"],
                             "vat_registration": [_drop_empty({"ic_dph": v["ic_dph"], "kind": v["kind"], "registered": v["registered"]})
                                                  for v in ix.vat_registrations(None, [g["dic"]])]})
                for g in groups
            ],
            "group_vat_returns": _vat_rows(group_returns if vat_history else group_returns[:VAT_RETURNS // 2], True),
            "note": "This taxpayer represents a VAT group (matched by the group's name): the group registers and files VAT "
                    "for all its members. The lists do not name the other members.",
        }
    debtors = ix.debtors_named(names)
    if debtors:
        out["tax_debtor_list"] = {
            "matched_by": "name",
            "entries": [{"name": d["name"], "debt": parse.eur(d["amount"]), "address": d["place"]} for d in debtors],
            "note": DEBTOR_NOTE,
        }
    out["lists_as_of"] = ix.as_of()
    cit = ix.cit_coverage()
    if cit:
        out["corporate_tax_coverage"] = (f"tax periods ending {cit['periods_to'][0]} to {cit['periods_to'][1]}, from the quarterly lists "
                                         f"since {cit['oldest_list']} (the published archive starts with the list of 2018-06-30); a period missing here "
                                         "was not published in these lists.")
    vat = ix.vat_returns_coverage()
    if vat:
        out["vat_returns_coverage"] = (f"from the quarterly lists {vat['lists'][0]} to {vat['lists'][1]} (the published archive starts "
                                       "with the list of 2018-06-30); each holds the returns filed that quarter, with late returns for older periods.")
    out.update(_stale(ix))
    out["source"] = LICENCE
    return {k: v for k, v in out.items() if v not in ([], None)}


def _vat_rows(rows: list[dict], with_dic: bool) -> list[dict]:
    """VAT return rows for output; list_of only where a period was published more than once."""
    seen: dict[tuple, int] = {}
    for r in rows:
        seen[(r["dic"], r["period"])] = seen.get((r["dic"], r["period"]), 0) + 1
    return [
        _drop_empty({**({"dic": r["dic"]} if with_dic else {}), "period": r["period"],
                     "excess_deduction": parse.eur(r["excess"]) or None, "own_tax_liability": parse.eur(r["own"]) or None,
                     **({"list_of": r["release"]} if seen[(r["dic"], r["period"])] > 1 else {})})
        for r in rows
    ]


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def fs_search_debtors(
    name: Annotated[str, Field(description="Words of the debtor's name; all must occur as word beginnings, ignoring case and diacritics (Siroky finds Široký).")],
    town: Annotated[str | None, Field(description="Part of the address (town, street or PSČ), ignoring case and diacritics.")] = None,
    min_debt: Annotated[float | None, Field(description="Only debts of at least this many EUR.")] = None,
    page: Annotated[int, Field(ge=0, description="0-based result page; 20 per page, largest debts first.")] = 0,
) -> dict:
    """Search the tax authority's list of tax debtors (zoznam daňových dlžníkov: companies and people
    with tax arrears) by name. The list has no IČO, so every result is matched by name only: compare the
    address before treating it as a given company or person."""
    ix = index()
    rows, total = ix.search_debtors(
        name, town=town, min_amount=round(min_debt * 100) if min_debt is not None else None,
        limit=PAGE_SIZE, offset=page * PAGE_SIZE,
    )
    return {
        "total": total,
        "page": page,
        "matched_by": "name",
        "results": [{"name": r["name"], "debt": parse.eur(r["amount"]), "address": r["place"]} for r in rows],
        "has_more": (page + 1) * PAGE_SIZE < total,
        "list_as_of": ix.lists().get("ds_dsdd", {}).get("as_of"),
        "note": DEBTOR_NOTE,
        **_stale(ix),
        "source": LICENCE,
    }


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def fs_list_flagged(
    flag: Annotated[Literal["struck_off", "cancellation_reasons", "less_reliable"], Field(description="struck_off: VAT payers whose registration was cancelled for breaking their duties (§ 52(8)); cancellation_reasons: VAT payers with reasons to cancel their registration (§ 52(6)); less_reliable: rated menej spoľahlivý by the index daňovej spoľahlivosti.")],
    town: Annotated[str | None, Field(description="Part of the address (town, street or PSČ), ignoring case and diacritics.")] = None,
    date_from: Annotated[str | None, Field(description="YYYY-MM-DD: struck off (struck_off) or published (cancellation_reasons) on or after this day. Not for less_reliable.")] = None,
    date_to: Annotated[str | None, Field(description="YYYY-MM-DD: on or before this day.")] = None,
    page: Annotated[int, Field(ge=0, description="0-based result page; 20 per page, newest first (less_reliable: by name).")] = 0,
) -> dict:
    """List taxpayers the tax authority flags, by date or place: VAT payers struck off or facing
    cancellation of their VAT registration, or rated menej spoľahlivý (less reliable). Each row has an
    IČO where published, for fs_get_taxpayer or other registers."""
    for value in (date_from, date_to):
        if value:
            date.fromisoformat(value)
    if flag == "less_reliable" and (date_from or date_to):
        raise ValueError("The reliability index has no dates; filter less_reliable by town only.")
    ix = index()
    out: dict = {}
    if flag != "less_reliable":
        out.update(await sync.refresh(ix, client()) or {})
    rows, total = ix.flagged(flag, town=town, date_from=date_from, date_to=date_to, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    list_name = {"struck_off": "ds_dphv", "cancellation_reasons": "ds_dphz", "less_reliable": "ds_iz_ran"}[flag]
    return {
        "total": total,
        "page": page,
        "results": [
            _drop_empty({"ico": r.get("ico"), "dic": r.get("dic"), "ic_dph": r.get("ic_dph"), "name": r["name"], "address": r["place"],
                         "violation_year": r.get("year"), "published": r.get("published"), "struck_off": r.get("struck_off")})
            for r in rows
        ],
        "has_more": (page + 1) * PAGE_SIZE < total,
        "list_as_of": ix.lists().get(list_name, {}).get("as_of"),
        **out,
        **_stale(ix),
        "source": LICENCE,
    }
