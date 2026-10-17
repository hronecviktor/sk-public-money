"""Build or refresh the local ITMS21+ index: sk-public-money-sync eu.

1. Lists, reloaded in full on every run because the API ignores modifiedSince: programmes, calls,
   subjects, projects, grant applications, payment claims, procurements and invoices. About 150
   requests of up to 1000 records, a few minutes.
2. Procurement details, one request each: which projects a procurement belongs to, its contracts
   and their main suppliers, CPV, procedure and bids. The first run fetches all of them (about
   13 000, some 4 hours at one request a second; it can be stopped and resumed). Later runs fetch
   those that are new or changed, plus at most 300 re-checks of details older than 30 days (open
   procurements) or 180 days (finished ones), since a contract can be added without the
   procurement's own change date moving.
3. Suppliers: the contracts and invoices name suppliers by IDs of a separate register with no list,
   so each new supplier is fetched once and cached.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime

from ...http import SourceUnavailable
from . import parse
from .client import INTERVAL, ITMSClient
from .index import EUIndex

# Procurement statuses after which contracts rarely change.
FINAL = ("Ukončené", "Stiahnuté", "Zrušené")
OPEN_AGE = 30 * 86400
CLOSED_AGE = 180 * 86400
# Details re-checked by age per run, on top of the new and changed ones.
RECHECK_PER_RUN = 300
# A list that comes back with fewer records than the API counts is not stored.
MIN_COMPLETE = 0.98
MAX_FAILURES = 5


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def _load(client: ITMSClient, entity: str, row, say, params: dict | None = None) -> list:
    rows, size = [], 0
    async for page, size in client.pages(entity, {"linkApi": "true", **(params or {})}):
        rows += [row(r) for r in page]
    if size and len(rows) < size * MIN_COMPLETE:
        raise SourceUnavailable(f"{entity}: got {len(rows)} of {size} records; not stored, try again later.")
    say(f"{entity}: {len(rows)} records")
    return rows


async def sync_lists(index: EUIndex, client: ITMSClient, say, *, invoices: bool = True) -> None:
    programmes = (await client.get_json("program", {"limit": -1})).get("results") or []
    calls = await _load(client, "vyzva", lambda r: {"id": r["id"], "code": r.get("kod"), "name": r.get("nazovSk"),
                                                    "programme_id": parse.ref(r.get("program"))}, say, {"ajUkoncene": "true"})
    index.replace_programmes(
        [{"id": p["id"], "code": p.get("kod"), "abbr": p.get("skratka"), "name": p.get("nazovSk")} for p in programmes], calls
    )
    index.replace_subjects(await _load(client, "subjekt", lambda r: (r["id"], parse.organisation(r)), say))
    projects = await _load(client, "projekt", parse.project_row, say)
    applications = await _load(client, "zonfp", parse.application_row, say)
    index.replace_items(projects, applications)
    index.replace_claims(await _load(client, "zop", parse.claim_row, say))
    index.replace_procurements(await _load(client, "verejneobstaravanie", parse.procurement_row, say))
    if invoices:
        index.replace_invoices(await _load(client, "uctovnydoklad", parse.invoice_row, say))
    index.set_meta("lists_synced_at", str(time.time()))


async def sync_details(index: EUIndex, client: ITMSClient, say, *, limit: int | None = None, everything: bool = False) -> int:
    if everything:
        due = index.procurements_due(open_age=0, closed_age=0, final=FINAL)
    else:
        due = index.procurements_due(open_age=OPEN_AGE, closed_age=CLOSED_AGE, final=FINAL, recheck=RECHECK_PER_RUN)
    due = due[:limit] if limit is not None else due
    say(f"procurement details: {len(due)} to fetch")
    started, failures = time.monotonic(), 0
    for n, (pid, updated) in enumerate(due, 1):
        try:
            raw = await client.procurement(pid)
        except LookupError:
            say(f"procurement {pid}: no longer in the API, skipped")
            continue
        except SourceUnavailable as exc:
            failures += 1
            say(f"procurement {pid} FAILED: {exc}")
            if failures >= MAX_FAILURES:
                raise
            continue
        failures = 0
        index.add_procurement_detail(pid, parse.procurement_detail(raw), updated)
        if n % 250 == 0 or n == len(due):
            left = (time.monotonic() - started) / n * (len(due) - n)
            say(f"[{n}/{len(due)}] procurement details, ~{left / 60:.0f} min left")
    return len(due)


async def sync_suppliers(index: EUIndex, client: ITMSClient, say, *, limit: int | None = None) -> int:
    todo = index.unknown_suppliers()
    todo = todo[:limit] if limit is not None else todo
    say(f"suppliers: {len(todo)} to fetch")
    failures = 0
    for n, sid in enumerate(todo, 1):
        try:
            index.add_supplier(sid, parse.organisation(await client.supplier(sid)))
        except LookupError:
            index.add_supplier(sid, None)
        except SourceUnavailable as exc:
            failures += 1
            say(f"supplier {sid} FAILED: {exc}")
            if failures >= MAX_FAILURES:
                raise
            continue
        failures = 0
        if n % 500 == 0 or n == len(todo):
            say(f"[{n}/{len(todo)}] suppliers")
    return len(todo)


async def run(
    *, details: bool = True, max_details: int | None = None, everything: bool = False, invoices: bool = True,
    interval: float = INTERVAL, out=sys.stderr, index: EUIndex | None = None, client: ITMSClient | None = None,
) -> bool:
    """One sync run; returns whether it completed."""
    index = index or EUIndex()
    client = client or ITMSClient(interval=interval)
    say = lambda text: print(f"{_now()} {text}", file=out, flush=True)  # noqa: E731
    say(f"syncing ITMS21+ into {index.path}")
    try:
        await sync_lists(index, client, say, invoices=invoices)
        if details:
            await sync_details(index, client, say, limit=max_details, everything=everything)
            await sync_suppliers(index, client, say)
        index.rebuild_procurement_text()
        index.set_meta("synced_at", str(time.time()))
        say(f"done: {index.counts()}")
        return True
    except SourceUnavailable as exc:
        index.rebuild_procurement_text()
        say(f"STOPPED: {exc} Run again to continue; what was fetched is kept.")
        return False
    finally:
        await client.aclose()
        index.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sk-public-money-sync eu", description="Build or refresh the local index of EU funds 2021-2027 from ITMS21+.")
    parser.add_argument("--no-details", action="store_true", help="only reload the lists: no procurement details, no new suppliers")
    parser.add_argument("--max-details", type=int, help="fetch at most this many procurement details in this run")
    parser.add_argument("--all-details", action="store_true", help="fetch every procurement detail again, not only new, changed and old ones")
    parser.add_argument("--no-invoices", action="store_true", help="skip the invoice list (about 75 000 records)")
    parser.add_argument("--interval", type=float, default=INTERVAL, help=f"seconds between requests (default {INTERVAL}, minimum 1)")
    args = parser.parse_args(argv)
    ok = asyncio.run(run(details=not args.no_details, max_details=args.max_details, everything=args.all_details,
                         invoices=not args.no_invoices, interval=max(args.interval, 1.0)))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
