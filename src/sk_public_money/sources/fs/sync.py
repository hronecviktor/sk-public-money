"""Download the Finančná správa lists into the local index: sk-public-money-sync fs.

Every list is one zip (70 MB together); a list is downloaded only when its Last-Modified changed
since it was indexed, parsed as a stream and the zip deleted. The two quarterly lists, corporate
income tax and VAT returns, also add their archived quarterly files since --since (default 2016; the
archives start with the lists of June 2018, so all of them), so a company's history shows; about 33 files of 7 MB and 33 of 15-18 MB, each
downloaded once. The tools top up the two small daily VAT lists (cancellation reasons
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
from .client import ARCHIVE_PAGES, FSClient, list_url
from .index import LISTS, FSIndex

# The user asked for history back to 2016; the published archives start with the lists of
# 30 June 2018, so this takes all of them.
DEFAULT_SINCE = "2016-01-01"
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


async def sync_archive(client: FSClient, index: FSIndex, name: str, since: str, *, say=None) -> list[str]:
    """Index the archived quarterly files of a list dated since a day that are not indexed yet."""
    key = f"{name}_archive"
    done = set(json.loads(index.get_meta(key) or "[]"))
    files = [(d, url) for d, url in await client.archive_files(name) if d >= since and d not in done]
    failed = []
    for n, (day, url) in enumerate(files, 1):
        path = raw_dir() / f"{name}_{day}.zip"
        try:
            localdb.ensure_free_space(path, 200 << 20)
            await client.download(url, path)
            started = time.monotonic()
            result = index.load(name, path, archive=True)
        except SourceUnavailable as exc:
            failed.append(f"{name} {day}: {exc}")
            if say:
                say(f"[{n}/{len(files)}] {name} archive {day} FAILED: {exc}")
            continue
        finally:
            path.unlink(missing_ok=True)
        done.add(day)
        index.set_meta(key, json.dumps(sorted(done)))
        if say:
            say(f"[{n}/{len(files)}] {name} archive {day}: {result['records']} records as of {result['as_of']} "
                f"in {time.monotonic() - started:.0f} s")
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
            if name in ARCHIVE_PAGES and archive:
                try:
                    failed += await sync_archive(client, index, name, since, say=say)
                except SourceUnavailable as exc:
                    failed.append(f"{name} archive: {exc}")
                    say(f"{name} archive FAILED: {exc}")
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
    parser.add_argument("--since", default=DEFAULT_SINCE, help=f"oldest archived quarterly file (corporate tax, VAT returns) to add, YYYY-MM-DD (default {DEFAULT_SINCE}; the archives start in June 2018)")
    parser.add_argument("--no-archive", dest="archive", action="store_false", help="skip the archived quarterly files")
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
