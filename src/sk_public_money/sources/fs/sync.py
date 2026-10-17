"""Download the Finančná správa lists into the local index: sk-public-money-sync fs.

Every list is one zip (70 MB together); a list is downloaded only when its Last-Modified changed
since it was indexed, parsed as a stream and the zip deleted. The corporate income tax list adds the
quarterly archive files since --since (default 2023), so a company's tax shows over several years;
each archive file is about 7 MB. The tools top up the two small daily VAT lists (cancellation reasons
and struck off) inline when they are stale; everything else is this command's job.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from ... import localdb
from ...http import SourceUnavailable, cache_dir
from .client import FSClient, list_url
from .index import LISTS, FSIndex

DEFAULT_SINCE = "2023-01-01"
# Process order: older corporate tax files first, then lists whose names are most current last, so
# the names in `subjects` come from the newest list.
ORDER = ["ds_dppos", "ds_dphno", "ds_dsdd", "ds_dphz", "ds_dphv", "ds_dphs", "ds_iz_ran"]
INLINE_LISTS = ("ds_dphz", "ds_dphv")
CHECK_EVERY = 6 * 3600


def raw_dir() -> Path:
    return cache_dir() / "fs" / "raw"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def sync_list(client: FSClient, index: FSIndex, name: str, *, force: bool = False, say=None) -> dict | None:
    """Download and index one list when it changed; returns its counts, or None when unchanged."""
    url = list_url(name)
    modified, size = await client.last_modified(url)
    known = index.lists().get(name)
    if not force and known and modified and known["last_modified"] == modified:
        return None
    path = raw_dir() / f"{name}.zip"
    # The zip and its streamed index rows; the XML is never unpacked to disk.
    localdb.ensure_free_space(path, (size or 50 << 20) * 3)
    if say:
        say(f"{name}: downloading {((size or 0) / 2**20):.1f} MB")
    await client.download(url, path)
    try:
        started = time.monotonic()
        result = index.load(name, path, last_modified=modified)
    finally:
        path.unlink(missing_ok=True)
    if say:
        say(f"{name}: {result['records']} records as of {result['as_of']} ({result['skipped']} skipped) in {time.monotonic() - started:.0f} s")
    return result


async def sync_archive(client: FSClient, index: FSIndex, since: str, *, say=None) -> list[str]:
    """Index the archived corporate tax files dated since a day that are not indexed yet."""
    done = set(json.loads(index.get_meta("dppos_archive") or "[]"))
    files = [(d, url) for d, url in await client.archive_files() if d >= since and d not in done]
    failed = []
    for n, (day, url) in enumerate(files, 1):
        path = raw_dir() / f"ds_dppos_{day}.zip"
        try:
            localdb.ensure_free_space(path, 60 << 20)
            await client.download(url, path)
            result = index.load("ds_dppos", path, archive=True)
        except SourceUnavailable as exc:
            failed.append(f"{day}: {exc}")
            if say:
                say(f"[{n}/{len(files)}] ds_dppos {day} FAILED: {exc}")
            continue
        finally:
            path.unlink(missing_ok=True)
        done.add(day)
        index.set_meta("dppos_archive", json.dumps(sorted(done)))
        if say:
            say(f"[{n}/{len(files)}] ds_dppos archive {day}: {result['records']} records as of {result['as_of']}")
    return failed


async def refresh(index: FSIndex, client: FSClient) -> dict | None:
    """Inline top-up for the tools: the small daily VAT flag lists, at most every CHECK_EVERY.
    Returns a note for the tool result when that failed."""
    if not localdb.is_due(index.db, "inline_checked_at", CHECK_EVERY):
        return None
    try:
        for name in INLINE_LISTS:
            await sync_list(client, index, name)
    except (SourceUnavailable, OSError) as exc:
        return {"update_note": f"Could not update the VAT flag lists: {exc}"}
    localdb.mark_done(index.db, "inline_checked_at")
    return None


async def run(lists: list[str], since: str, *, force: bool = False, archive: bool = True, out=sys.stderr,
              index: FSIndex | None = None, client: FSClient | None = None) -> list[str]:
    index = index or FSIndex()
    client = client or FSClient()
    say = lambda text: print(f"{_now()} {text}", file=out, flush=True)  # noqa: E731
    failed: list[str] = []
    started = time.monotonic()
    try:
        for name in [n for n in ORDER if n in lists]:
            if name == "ds_dppos" and archive:
                try:
                    failed += await sync_archive(client, index, since, say=say)
                except SourceUnavailable as exc:
                    failed.append(f"ds_dppos archive: {exc}")
                    say(f"ds_dppos archive FAILED: {exc}")
            try:
                if await sync_list(client, index, name, force=force, say=say) is None:
                    say(f"{name}: unchanged")
            except SourceUnavailable as exc:
                failed.append(f"{name}: {exc}")
                say(f"{name} FAILED: {exc}")
        localdb.mark_done(index.db, "inline_checked_at")
        # Replacing whole lists leaves a write-ahead log as large as the lists; give the space back.
        index.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size = index.path.stat().st_size / 2**20
        say(f"done in {(time.monotonic() - started) / 60:.1f} min; index {index.path} is {size:.0f} MB")
        if failed:
            say(f"{len(failed)} downloads failed (run again to retry):")
            for line in failed:
                print(f"  {line}", file=out, flush=True)
    finally:
        await client.aclose()
        index.close()
    return failed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sk-public-money-sync fs", description="Download the Finančná správa SR lists (VAT, tax reliability, debtors, corporate tax) into the local index.")
    parser.add_argument("--lists", default=",".join(ORDER), help=f"comma-separated lists to update (default all: {','.join(ORDER)})")
    parser.add_argument("--since", default=DEFAULT_SINCE, help=f"oldest archived corporate tax file to add, YYYY-MM-DD (default {DEFAULT_SINCE}; the archive starts in 2018)")
    parser.add_argument("--no-archive", dest="archive", action="store_false", help="skip the archived corporate tax files")
    parser.add_argument("--force", action="store_true", help="download and index the lists again even when unchanged")
    args = parser.parse_args(argv)
    lists = [n.strip() for n in args.lists.split(",") if n.strip()]
    unknown = [n for n in lists if n not in LISTS]
    if unknown:
        parser.error(f"unknown lists: {', '.join(unknown)}")
    datetime.strptime(args.since, "%Y-%m-%d")
    sys.exit(1 if asyncio.run(run(lists, args.since, force=args.force, archive=args.archive)) else 0)


if __name__ == "__main__":
    main()
