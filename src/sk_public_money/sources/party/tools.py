"""Tools for political-party financing: transparent accounts and annual reports."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from ...text import fold
from . import parse
from .client import MAX_PAGE, SLSP_WEB_URL, SLSPClient

IBAN = re.compile(r"SK\d{22}")
ACCOUNT_NOTE = (
    "Slovenská sporiteľňa transparent accounts only; the list also holds candidates', campaigns' and "
    "charities' accounts. Parties' accounts at ČSOB, VÚB, Tatra banka or Fio are not covered."
)
COUNTERPARTY_NOTE = (
    "Counterparties are named as the bank shows them, with no IBAN or IČO: a matching name is not proof "
    "of the same person or company."
)

_slsp: SLSPClient | None = None


def slsp() -> SLSPClient:
    global _slsp
    if _slsp is None:
        _slsp = SLSPClient()
    return _slsp


def _day(value: str | None, name: str) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise ValueError(f"{name} must be a date YYYY-MM-DD, got: {value}") from None


def match_accounts(accounts: list[dict], query: str) -> list[dict]:
    """Accounts whose name contains every word of query, ignoring case and diacritics; exact (folded)
    names first, then the most recently updated."""
    words = fold(query).split()
    if not words:
        return []
    hits = [a for a in accounts if all(w in fold(a.get("name") or "") for w in words)]
    hits.sort(key=lambda a: a.get("updated") or "", reverse=True)
    hits.sort(key=lambda a: " ".join(fold(a.get("name") or "").split()) != " ".join(words))
    return hits


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def party_account_transactions(
    iban: Annotated[str | None, Field(description="IBAN of a Slovenská sporiteľňa transparent account (SK..., spaces allowed).")] = None,
    account: Annotated[str | None, Field(description="Words of the account name instead of iban, any case and diacritics (e.g. 'smer volby 2026'). Several matches are listed for you to pick an IBAN.")] = None,
    counterparty: Annotated[str | None, Field(description="Part of the counterparty name (payer or payee), any case and diacritics.")] = None,
    date_from: Annotated[str | None, Field(description="First day, YYYY-MM-DD.")] = None,
    date_to: Annotated[str | None, Field(description="Last day, YYYY-MM-DD.")] = None,
    min_amount: Annotated[float | None, Field(ge=0, description="Only movements of at least this many euros (in either direction unless direction is set).")] = None,
    direction: Annotated[Literal["any", "in", "out"], Field(description="in = received (donations, transfers in), out = paid.")] = "any",
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    page: Annotated[int, Field(ge=0, description="0-based page of results.")] = 0,
) -> dict:
    """Movements on a transparent account (transparentný účet) at Slovenská sporiteľňa, newest first:
    date, signed amount in EUR (negative = paid out), counterparty name, note, variable symbol. Parties
    and candidates must run campaigns through transparent accounts. Without iban or account, nothing is
    searched: give one."""
    if (iban is None) == (account is None):
        raise ValueError("Give exactly one of iban or account.")
    accounts = await slsp().accounts()
    if iban is not None:
        iban = iban.replace(" ", "").upper()
        if not IBAN.fullmatch(iban):
            raise ValueError(f"A Slovak IBAN is SK followed by 22 digits, got: {iban}")
        found = [a for a in accounts if a.get("iban") == iban]
        if not found:
            raise LookupError(f"{iban} is not a published Slovenská sporiteľňa transparent account. {ACCOUNT_NOTE}")
    else:
        found = match_accounts(accounts, account)
        if not found:
            raise LookupError(f"No Slovenská sporiteľňa transparent account is named like '{account}'. {ACCOUNT_NOTE}")
        if len(found) > 1:
            return {
                "accounts": [parse.account(a) for a in found[:50]],
                "matching_accounts": len(found),
                "note": "Several accounts match; call again with the iban of one. " + ACCOUNT_NOTE,
            }
    acct = found[0]
    query = {
        "counterparty": (counterparty or "").strip() or None,
        "date_from": _day(date_from, "date_from"),
        "date_to": _day(date_to, "date_to"),
    }
    least = max(min_amount or 0.0, 0.01)
    offset = page * limit
    if offset + limit > MAX_PAGE * 20:
        raise ValueError("That page is too deep; narrow the search by date or counterparty.")
    if direction == "any" and min_amount:
        # The API filters by signed amount, so large movements both ways take two queries, merged here.
        size = offset + limit
        if size > MAX_PAGE:
            raise ValueError(f"With min_amount in both directions, at most {MAX_PAGE} results can be paged; set direction or narrow by date.")
        got_in = await slsp().turnovers(acct["iban"], size=size, amount_from=least, **query)
        got_out = await slsp().turnovers(acct["iban"], size=size, amount_to=-least, **query)
        rows = got_in.get("turnovers", []) + got_out.get("turnovers", [])
        rows.sort(key=lambda t: (t.get("date") or "", int(t.get("turnoverId") or 0)), reverse=True)
        rows = rows[offset : offset + limit]
        total = (got_in.get("page") or {}).get("totalElements", 0) + (got_out.get("page") or {}).get("totalElements", 0)
    else:
        bounds = {}
        if direction == "in":
            bounds["amount_from"] = least
        elif direction == "out":
            bounds["amount_to"] = -least
        got = await slsp().turnovers(acct["iban"], page=page, size=limit, **query, **bounds)
        rows = got.get("turnovers", [])
        total = (got.get("page") or {}).get("totalElements", 0)
    out = {
        "account": parse.account(acct),
        "matching": total,
        "transactions": [parse.turnover(t) for t in rows],
        "has_more": offset + len(rows) < total,
        "source": f"Slovenská sporiteľňa transparent accounts ({SLSP_WEB_URL})",
        "note": COUNTERPARTY_NOTE,
    }
    if query["counterparty"]:
        out["matched_by"] = "name"
    return out
