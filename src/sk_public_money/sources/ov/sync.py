"""Build and top up the Obchodný vestník index from Slovensko.Digital's Datahub.

Datahub keeps every OV notice since June 2016 as the portal's original XML (table ov.raw_issues).
Its parsed tables are not used: likvidator_issues stops in 2017 and or_podanie_issues in 2019.

- Build: the weekly Postgres dump (ov.sql.gz, about 4.2 GB) is streamed over HTTP, decompressed and
  read COPY block by COPY block on the fly; nothing of it is written to disk. Only kept notices are
  stored (see parse.py). Fetching the same rows from the sync API would take ~42,000 requests at
  its limit of 60 per minute (about 12 hours), so the dump is used for the first load.
- Top-up: the raw_issues sync API, from the newest change the index has seen (since + last_id
  cursor, Link header paging, 100 rows per page). The tools fetch a few pages inline when due.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from ... import localdb
from ...http import USER_AGENT, SourceUnavailable, ThrottledClient
from . import parse
from .index import OVIndex

DUMP_URL = "https://s3.eu-central-1.amazonaws.com/ekosystem-slovensko-digital-dumps/ov.sql.gz"
SYNC_URL = "https://datahub.ekosystem.slovensko.digital/api/data/ov/raw_issues/sync"
INTERVAL = 1.1  # the API allows 60 requests per minute
CHECK_EVERY = 6 * 3600
INLINE_PAGES = 5
INDEX_ESTIMATE = 1 << 30  # disk space the build may need
log = logging.getLogger(__name__)

_ESCAPE = re.compile(r"\\(?:([0-7]{1,3})|x([0-9a-fA-F]{1,2})|(.))")
_SIMPLE = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def unescape(field: str) -> str | None:
    """A field of a Postgres COPY text row: \\N is NULL, backslash escapes are decoded."""
    if field == "\\N":
        return None
    if "\\" not in field:
        return field

    def repl(m: re.Match) -> str:
        if m.group(1):
            return chr(int(m.group(1), 8))
        if m.group(2):
            return chr(int(m.group(2), 16))
        return _SIMPLE.get(m.group(3), m.group(3))

    return _ESCAPE.sub(repl, field)


COPY = re.compile(rb"^COPY (\S+) \(([^)]*)\) FROM stdin;$")


class DumpReader:
    """Feed decompressed SQL bytes in; get (table, row dict) for the tables you ask for.

    Rows of other tables are skipped without decoding. raw_filter(file_name) may skip raw_issues
    rows before their (large) content is decoded."""

    def __init__(self, tables: set[str], raw_filter=None):
        self.tables = tables
        self.raw_filter = raw_filter
        self.buffer = b""
        self.table: str | None = None
        self.columns: list[str] = []
        self.rows = 0

    def feed(self, data: bytes):
        self.buffer += data
        *lines, self.buffer = self.buffer.split(b"\n")
        for line in lines:
            yield from self._line(line)

    def close(self):
        if self.buffer:
            yield from self._line(self.buffer)
            self.buffer = b""

    def _line(self, line: bytes):
        if self.table is None:
            m = COPY.match(line)
            if m:
                self.table = m.group(1).decode()
                self.columns = [c.strip().strip('"') for c in m.group(2).decode().split(",")]
            return
        if line == b"\\.":
            self.table = None
            return
        if self.table not in self.tables:
            return
        self.rows += 1
        if self.table == "ov.raw_issues" and self.raw_filter is not None:
            # id, bulletin_issue_id, file_name, ...: look at the file name before decoding the rest.
            head = line.split(b"\t", 3)
            name_at = self.columns.index("file_name")
            if len(head) > name_at and not self.raw_filter(head[name_at].decode("utf-8", "replace")):
                yield self.table, self._light(line)
                return
        fields = line.decode("utf-8", "replace").split("\t")
        yield self.table, {c: unescape(v) for c, v in zip(self.columns, fields)}

    def _light(self, line: bytes) -> dict:
        """The id and updated_at of a skipped raw row (for the sync cursor), without its content."""
        fields = line.rsplit(b"\t", 2)
        first = line.split(b"\t", 1)[0]
        return {"id": first.decode(), "updated_at": fields[-1].decode() if len(fields) == 3 else None, "skipped": True}


def _wanted(file_name: str) -> bool:
    return parse.category_of(parse.kind_of(file_name)) is not None


def _published(issue: dict | None) -> tuple[str, str]:
    """(YYYY-MM-DD, 'number/year') of a bulletin issue."""
    if not issue:
        return "", ""
    return str(issue["published_at"])[:10], f"{issue['number']}/{issue['year']}"


def _iso(ts: str | None) -> str | None:
    """A dump timestamp ('2026-10-10 03:02:27.235828') in the API's form ('...T...Z')."""
    if not ts:
        return None
    return ts.replace(" ", "T") + ("" if ts.endswith("Z") else "Z")


def build_from_dump(index: OVIndex, url: str = DUMP_URL, *, http: httpx.Client | None = None, retries: int = 5) -> dict:
    """Stream the dump into the index. Returns counts. Resumes an interrupted download by byte range."""
    own = http is None
    http = http or httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0), headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    issues: dict[str, dict] = {}
    stats = {"raw_rows": 0, "kept": 0, "skipped_kinds": {}, "kept_kinds": {}}
    cursor = ("", 0)  # (updated_at, id) of the newest raw row seen
    reader = DumpReader({"ov.bulletin_issues", "ov.raw_issues"}, raw_filter=_wanted)
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    offset, attempt, started, total_size = 0, 0, time.monotonic(), None
    last_report = 0

    def handle(table: str, row: dict) -> None:
        nonlocal cursor
        if table == "ov.bulletin_issues":
            issues[row["id"]] = {"published_at": row["published_at"], "number": row["number"], "year": row["year"]}
            return
        stats["raw_rows"] += 1
        updated = _iso(row.get("updated_at")) or ""
        if (updated, int(row["id"])) > cursor:
            cursor = (updated, int(row["id"]))
        if row.get("skipped"):
            return
        published, issue = _published(issues.get(row["bulletin_issue_id"]))
        record = parse.parse(int(row["id"]), row["file_name"], row["content"], published, issue)
        kind = parse.kind_of(row["file_name"]) or "?"
        if record:
            index.add(record)
            stats["kept"] += 1
            stats["kept_kinds"][kind] = stats["kept_kinds"].get(kind, 0) + 1
            if stats["kept"] % 5000 == 0:
                index.commit()
        else:
            stats["skipped_kinds"][kind] = stats["skipped_kinds"].get(kind, 0) + 1

    try:
        while True:
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                with http.stream("GET", url, headers=headers) as resp:
                    if resp.status_code not in (200, 206):
                        raise SourceUnavailable(f"The Datahub dump answered HTTP {resp.status_code}.")
                    if offset and resp.status_code != 206:
                        raise SourceUnavailable("The Datahub dump server does not resume downloads; start again.")
                    if total_size is None:
                        total_size = int(resp.headers.get("Content-Length") or 0) or None
                        localdb.ensure_free_space(index.path, INDEX_ESTIMATE)
                    for chunk in resp.iter_bytes(1 << 20):
                        offset += len(chunk)
                        data = decompressor.decompress(chunk)
                        while decompressor.eof and decompressor.unused_data:  # a multi-member gzip file
                            rest = decompressor.unused_data
                            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                            data += decompressor.decompress(rest)
                        for table, row in reader.feed(data):
                            handle(table, row)
                        if offset - last_report >= 200 << 20:
                            last_report = offset
                            share = f"{100 * offset / total_size:.0f} %" if total_size else f"{offset >> 20} MB"
                            log.warning("dump %s, %d raw notices read, %d kept, %.0f min", share, stats["raw_rows"], stats["kept"], (time.monotonic() - started) / 60)
                break
            except (httpx.TransportError, httpx.RemoteProtocolError) as exc:
                attempt += 1
                if attempt > retries:
                    raise SourceUnavailable(f"The Datahub dump download failed {attempt} times ({exc}).") from exc
                log.warning("download interrupted at %d MB (%s); resuming", offset >> 20, exc)
                time.sleep(5 * attempt)
        for table, row in reader.close():
            handle(table, row)
    finally:
        index.commit()
        if own:
            http.close()
    if cursor[0]:
        index.set_meta("cursor_since", cursor[0])
        index.set_meta("cursor_last_id", str(cursor[1]))
    index.set_meta("built", str(time.time()))
    localdb.mark_done(index.db, "checked")
    stats["minutes"] = round((time.monotonic() - started) / 60, 1)
    return stats


class DatahubClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None, interval: float = INTERVAL):
        super().__init__(lambda: interval, http=http, cache=cache)

    async def page(self, since: str, last_id: int) -> tuple[list[dict], bool]:
        """One page of raw issues changed after (since, last_id), and whether more follow."""
        params = {"since": since}
        if last_id:
            params["last_id"] = str(last_id)
        resp = await self._get(SYNC_URL, params)
        if resp.status_code == 429:
            raise SourceUnavailable("Datahub's rate limit was reached; try again in a minute.")
        if resp.status_code >= 400:
            raise SourceUnavailable(f"datahub.ekosystem.slovensko.digital returned HTTP {resp.status_code}.")
        rows = resp.json()
        link = resp.headers.get("Link") or ""
        return rows, "rel='next'" in link or 'rel="next"' in link


def _next_cursor(link_rows: list[dict]) -> tuple[str, int]:
    last = link_rows[-1]
    return last["updated_at"], int(last["id"])


async def top_up(index: OVIndex, client: DatahubClient, *, max_pages: int | None = None) -> dict:
    """Fetch changed raw issues since the index's cursor. Returns counts and whether it caught up."""
    since = index.get_meta("cursor_since")
    if not since:
        raise LookupError(f"The OV index has no sync cursor; build it with {localdb.sync_command('ov')}.")
    last_id = int(index.get_meta("cursor_last_id") or 0)
    pages = kept = 0
    more = True
    while more and (max_pages is None or pages < max_pages):
        rows, more = await client.page(since, last_id)
        pages += 1
        for row in rows:
            published, issue = _published(row.get("bulletin_issue"))
            record = parse.parse(int(row["id"]), row.get("file_name"), row.get("content"), published, issue)
            if record:
                index.add(record, replace_links=True)
                kept += 1
            else:
                index.drop(int(row["id"]))
        if rows:
            since, last_id = _next_cursor(rows)
            index.set_meta("cursor_since", since)
            index.set_meta("cursor_last_id", str(last_id))
        index.commit()
        if not rows:
            more = False
    localdb.mark_done(index.db, "checked")
    return {"pages": pages, "kept": kept, "caught_up": not more}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sk-public-money-sync ov", description=(
        "Build or top up the Obchodný vestník index from Slovensko.Digital's Datahub. The first run streams the "
        "weekly dump (about 4.2 GB, nothing stored but the index); later runs fetch changes from the sync API."))
    parser.add_argument("--rebuild", action="store_true", help="stream the dump again even when the index exists")
    parser.add_argument("--dump-url", default=DUMP_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    index = OVIndex()
    try:
        if args.rebuild or not index.get_meta("built"):
            print("Streaming the Datahub OV dump (about 4.2 GB); this takes a while…", file=sys.stderr)
            stats = build_from_dump(index, args.dump_url)
            print(f"Built in {stats['minutes']} min: {stats['raw_rows']} notices read, {stats['kept']} kept.", file=sys.stderr)
            for kind, n in sorted(stats["kept_kinds"].items(), key=lambda kv: -kv[1]):
                print(f"  kept {n:>9}  {kind}", file=sys.stderr)
            for kind, n in sorted(stats["skipped_kinds"].items(), key=lambda kv: -kv[1])[:25]:
                print(f"  not kept {n:>9}  {kind} (parsed, but no subject IČO or not a kept kind/date)", file=sys.stderr)

        async def run() -> dict:
            client = DatahubClient()
            try:
                return await top_up(index, client)
            finally:
                await client.aclose()

        result = asyncio.run(run())
        first, last = index.coverage()
        print(f"Topped up: {result['pages']} pages, {result['kept']} notices kept. Index covers {first} to {last}.", file=sys.stderr)
    finally:
        index.close()
