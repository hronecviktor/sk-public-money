"""Turn transparent-account records into compact, readable dicts."""

from __future__ import annotations


def money(value: dict | None) -> float | None:
    """{"value": -61500, "precision": 2, "currency": "EUR"} -> -615.0"""
    if not value or value.get("value") is None:
        return None
    return round(value["value"] / 10 ** (value.get("precision") or 0), 2)


def _clean(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return value or None


def account(raw: dict) -> dict:
    row = {
        "name": _clean(raw.get("name")),
        "iban": raw.get("iban"),
        "balance_eur": money(raw.get("balance")),
        "currency": (raw.get("balance") or {}).get("currency"),
        "opened": (raw.get("created") or "")[:10] or None,
        "updated": (raw.get("updated") or "")[:19] or None,
    }
    if row["currency"] == "EUR":
        del row["currency"]
    return {k: v for k, v in row.items() if v is not None}


def turnover(raw: dict) -> dict:
    """One account movement. The running balance is not published (always 0), so it is dropped."""
    row = {
        "date": raw.get("date"),
        "amount_eur": money(raw.get("amount")),
        "currency": (raw.get("amount") or {}).get("currency"),
        "counterparty": _clean(raw.get("counterAccountName")),
        "note": _clean(raw.get("note")),
        "type": _clean(raw.get("description")),
        "vs": _clean(raw.get("variable")),
        "ss": _clean(raw.get("specific")),
        "id": raw.get("turnoverId"),
    }
    if row["currency"] == "EUR":
        del row["currency"]
    return {k: v for k, v in row.items() if v is not None}
