"""Current and former entries of a register record (directors, owners, capital…), kept apart.

Registers record every change as a new dated entry, and sometimes record the same one twice (RPO
has a director ending on 9 and on 13 November 2023, a deposit listed twice). Mixing ended entries
into the current list makes former directors look current, so they always go to a separate list.
"""

from __future__ import annotations

import json


def _key(item: dict, *, without: tuple[str, ...] = ()) -> str:
    return json.dumps({k: v for k, v in item.items() if k not in without}, sort_keys=True, ensure_ascii=False, default=str)


def dedupe(items: list[dict]) -> list[dict]:
    """Drop entries identical to an earlier one."""
    seen: set[str] = set()
    out = []
    for item in items:
        key = _key(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def collapse_ended(items: list[dict]) -> list[dict]:
    """Ended entries, newest end first; entries that differ only in their end date (the register
    recorded the same ending twice) are kept once, with the later date."""
    latest: dict[str, dict] = {}
    for item in items:
        key = _key(item, without=("valid_to",))
        if key not in latest or (item.get("valid_to") or "") > (latest[key].get("valid_to") or ""):
            latest[key] = item
    return sorted(latest.values(), key=lambda i: i.get("valid_to") or "", reverse=True)


def split(items: list[dict], ended: list[bool], include_history: bool) -> tuple[list[dict], list[dict] | int]:
    """(current entries, former entries) with include_history, else (current entries, how many former)."""
    current = dedupe([i for i, e in zip(items, ended) if not e])
    former = collapse_ended([i for i, e in zip(items, ended) if e])
    return current, (former if include_history else len(former))
