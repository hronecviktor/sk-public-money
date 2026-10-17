"""sk-public-money-sync court: copy the register of disqualified persons into the local index.

The list (about a thousand records) takes two requests; each record's detail (ban period, case
number) one more, spaced a second apart, so a first sync takes about 17 minutes. Later runs fetch
details only for new records and for those not refreshed in 30 days. The tools top up the list
themselves when it is a day old and fetch missing details of the records they show or match.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime

from . import disqualified
from .client import CourtClient
from ...http import SourceUnavailable


def _say(out, text: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}", file=out, flush=True)


async def sync(*, full: bool = False, out=sys.stderr, client: CourtClient | None = None) -> dict:
    client = client or CourtClient()
    db = disqualified.open_db()
    stats = {"records": 0, "details": 0}
    try:
        stats["records"] = await disqualified.refresh_list(db, client, force=True)
        _say(out, f"{stats['records']} records listed; index at {disqualified.path()}")
        max_age = 0 if full else disqualified.DETAIL_EVERY
        now = time.time()
        ids = [r["id"] for r in db.execute("SELECT id, detail_at FROM disqualified ORDER BY id")
               if full or not r["detail_at"] or now - r["detail_at"] >= max_age]
        _say(out, f"fetching {len(ids)} record details (about {len(ids)} s)")
        for i in range(0, len(ids), 50):
            await disqualified.fetch_details(db, client, ids[i : i + 50], max_age=max_age)
            stats["details"] += len(ids[i : i + 50])
            _say(out, f"{stats['details']}/{len(ids)} details")
    except SourceUnavailable as exc:
        _say(out, f"STOPPED: {exc}")
    finally:
        db.close()
        await client.aclose()
    return stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sk-public-money-sync court",
                                     description="Copy the register of disqualified persons (diskvalifikácie) into the local index.")
    parser.add_argument("--full", action="store_true", help="fetch every record's detail again")
    args = parser.parse_args(argv)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(sync(full=args.full))
