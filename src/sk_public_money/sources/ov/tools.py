"""Tool for the Obchodný vestník (Commercial Bulletin of the Ministry of Justice SR), from a local
index built from Slovensko.Digital's Datahub (see sync.py)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import Field

from ... import localdb
from ...app import READ_ONLY, mcp, user_errors
from ...http import SourceUnavailable
from . import index as index_mod
from . import parse, sync

NOTE = ("Údaje majú len informatívny charakter a nie sú právne záväzné (informative only, not legally binding). "
        "Source: Obchodný vestník (Ministry of Justice SR) via Slovensko.Digital Datahub, under its dataset terms "
        "(Podmienky používania datasetov, https://ekosystem.slovensko.digital/podmienky). This server modified the "
        "data: notices are shortened and restructured, as of the date in index_built.")
INSOLVENCY_NOTE = ("OV insolvency notices are kept only up to 30 September 2025 and cut short; for proceedings, "
                   "trustees and full texts use insolvency_search and insolvency_get.")
MENTIONED_NOTE = ("Notices with mentioned_only=true name the IČO only in their text (e.g. the other company in a "
                  "merger), not as the notice's subject.")

_index: index_mod.OVIndex | None = None
_client: sync.DatahubClient | None = None


def index() -> index_mod.OVIndex:
    global _index
    if _index is None:
        if not index_mod.exists():
            raise FileNotFoundError(
                "The Obchodný vestník index has not been built. Build it once with `sk-public-money-sync ov` "
                "(streams a 4.2 GB dump from Slovensko.Digital's Datahub; the index takes about 1 GB)."
            )
        _index = index_mod.OVIndex()
    return _index


def client() -> sync.DatahubClient:
    global _client
    if _client is None:
        _client = sync.DatahubClient()
    return _client


def normalize_ico(ico: str) -> str:
    value = parse.normalize_ico(ico)
    if value is None:
        raise ValueError(f"An IČO has up to 8 digits, got: {ico}")
    return value


async def _refresh(idx: index_mod.OVIndex) -> str | None:
    """Top up a few pages when due; return a note when the index is still behind."""
    if not localdb.is_due(idx.db, "checked", sync.CHECK_EVERY):
        return None
    try:
        result = await sync.top_up(idx, client(), max_pages=sync.INLINE_PAGES)
    except SourceUnavailable as exc:
        return f"Could not top up the index ({exc}); results may miss the newest notices."
    if not result["caught_up"]:
        return "The index is behind the Vestník; run `sk-public-money-sync ov` to catch up."
    return None


def _built(idx: index_mod.OVIndex) -> str | None:
    built = idx.get_meta("built")
    return datetime.fromtimestamp(float(built), timezone.utc).date().isoformat() if built else None


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def ov_search_notices(
    ico: Annotated[str, Field(description="IČO of the company (spaces allowed; shorter ones are zero-padded).")],
    kind: Annotated[Literal["liquidation", "dissolution", "capital", "insolvency", "register", "other"] | None, Field(
        description="liquidation: entering liquidation, call to creditors; dissolution: merger, split, conversion, "
                    "dissolution; capital: capital reduction, capital funds; insolvency: konkurz/restructuring notices "
                    "until Sept 2025; register: commercial register entries (new, change, deletion); other: other "
                    "notices and corrections.")] = None,
    since: Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Only notices published on or after this date (YYYY-MM-DD).")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 30,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """Notices about a company in the Obchodný vestník (the Ministry of Justice's Commercial Bulletin)
    since mid-2016, newest first: liquidation and calls to creditors (with liquidators), mergers,
    splits and conversions, capital reductions, commercial register entries, and konkurz notices.
    Each gives date, issue, type, the companies concerned and the text (shortened). Data from
    Slovensko.Digital's Datahub, kept in a local index."""
    wanted = normalize_ico(ico)
    idx = index()
    note = await _refresh(idx)
    rows, total = idx.search(wanted, category=kind, since=since, limit=limit, offset=offset)
    first, last = idx.coverage()
    for row in rows:
        row["category_name"] = parse.CATEGORY_NAMES.get(row["category"])
        row["source"] = f"https://datahub.ekosystem.slovensko.digital/api/data/ov/raw_issues/{row['id']}"
        if not row.get("mentioned_icos"):
            row.pop("mentioned_icos", None)
    notes = [NOTE]
    if any(r["category"] == "insolvency" for r in rows) or kind == "insolvency":
        notes.append(INSOLVENCY_NOTE)
    if any(r["mentioned_only"] for r in rows):
        notes.append(MENTIONED_NOTE)
    if note:
        notes.append(note)
    return {
        "ico": wanted,
        "total": total,
        "notices": rows,
        "has_more": offset + len(rows) < total,
        "index_covers": {"from": first, "to": last},
        "index_built": _built(idx),
        "notes": notes,
    }
