"""Download Vestník issues from data.slovensko.sk into the local index.

The national open-data catalogue lists one dataset per issue ("Vestník 207/2026", publisher ÚVO,
IČO 31797903) with a JSON download. Its SPARQL endpoint finds them; data.slovensko.sk publishes no
rate limit and its robots.txt allows everything, so issues are fetched one at a time with a pause.
JSON issues start with 201/2023 (17 October 2023), when ÚVO moved to eForms.

The full backfill (about 770 issues, 7 GB) runs from the command line: sk-public-money-sync uvo.
The tools only top up the newest few issues when the index is stale.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ...http import SourceUnavailable, ThrottledClient, cache_dir
from ...localdb import sync_command
from . import vestnik
from .index import VestnikIndex

SPARQL_URL = "https://data.slovensko.sk/api/sparql"
DOWNLOAD_HOST = "data.slovensko.sk"
UVO = "https://data.gov.sk/id/legal-subject/31797903"
INTERVAL = 2.0
FIRST_ISSUE_DATE = "2023-10-01"
# The tools check for new issues at most this often, and fetch at most this many inline.
CHECK_EVERY = 6 * 3600
INLINE_LIMIT = 5
# The backfill gives up after this many unreachable downloads in a row.
MAX_NETWORK_FAILURES = 5
TITLE = re.compile(r"Vestník\s+(\d+)/(\d{4})\s*$")
log = logging.getLogger(__name__)

LIST_QUERY = """
PREFIX dct: <http://purl.org/dc/terms/>
PREFIX dcat: <http://www.w3.org/ns/dcat#>
SELECT ?dataset ?title ?issued ?modified ?url WHERE {
  ?dataset a dcat:Dataset ; dct:title ?title ; dct:publisher <%(publisher)s> ; dcat:distribution ?d .
  ?d dcat:downloadURL ?url ; dct:format <http://publications.europa.eu/resource/authority/file-type/JSON> .
  OPTIONAL { ?dataset dct:issued ?issued }
  OPTIONAL { ?dataset dct:modified ?modified }
  FILTER (STRSTARTS(STR(?title), "Vestník "))
  %(since)s
}
"""


def raw_path(year: int, number: int) -> Path:
    return cache_dir() / "uvo" / "vestnik" / str(year) / f"{number}.json.gz"


class BadLink(ValueError):
    """The catalogue's download link for an issue is not a data.slovensko.sk address."""


class DuplicateUpload(ValueError):
    """A dataset holds an issue that is already indexed, under another issue's title."""


class CatalogueClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None, interval: float = INTERVAL):
        super().__init__(lambda: interval, http=http, cache=cache)

    async def list_issues(self, modified_since: str | None = None) -> list[dict]:
        """Every JSON Vestník issue in the catalogue (or those modified since a date): year, number,
        dataset, modified, url; oldest first."""
        since = f'FILTER (?modified >= "{modified_since}"^^<http://www.w3.org/2001/XMLSchema#dateTime>)' if modified_since else ""
        resp = await self._request(
            "GET", SPARQL_URL, params={"query": LIST_QUERY % {"publisher": UVO, "since": since}},
            headers={"Accept": "application/sparql-results+json"},
        )
        try:
            bindings = resp.json()["results"]["bindings"] if resp.status_code == 200 else None
        except (ValueError, KeyError, TypeError):
            bindings = None
        if bindings is None:
            raise SourceUnavailable(f"data.slovensko.sk SPARQL returned HTTP {resp.status_code} without results; try again later.")
        issues = {}
        for b in bindings:
            m = TITLE.match(b["title"]["value"].strip())
            if not m:
                continue
            key = (int(m.group(2)), int(m.group(1)))
            # Some datasets link over http, one (202/2023) to no real address at all.
            url = re.sub(rf"^http://{re.escape(DOWNLOAD_HOST)}/", f"https://{DOWNLOAD_HOST}/", b["url"]["value"].strip())
            issue = {
                "year": key[0], "number": key[1], "dataset": b["dataset"]["value"], "url": url,
                "issued": (b.get("issued") or {}).get("value"),
                "modified": (b.get("modified") or b.get("issued") or {}).get("value"),
            }
            # An issue re-uploaded as a second distribution: keep the newest.
            if key not in issues or (issue["modified"] or "") > (issues[key]["modified"] or ""):
                issues[key] = issue
        return [issues[k] for k in sorted(issues)]

    async def download_to(self, url: str, path: Path) -> None:
        """Stream an issue straight into a gzip file: no uncompressed copy is ever written."""
        if not url.startswith(f"https://{DOWNLOAD_HOST}/"):
            raise BadLink(f"the catalogue gives no usable download link ({url})")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        async with self._lock:  # one request at a time, spaced like every other
            wait = self._last + self._min_interval() - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._http.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        raise SourceUnavailable(f"data.slovensko.sk returned HTTP {resp.status_code} for {url}.")
                    with gzip.open(tmp, "wb", compresslevel=6) as out:
                        start = b""
                        async for chunk in resp.aiter_bytes(1 << 16):
                            if len(start) < 64:
                                start += chunk[:64]
                                if start.strip() and not start.lstrip().startswith(b"{"):
                                    raise SourceUnavailable(f"data.slovensko.sk sent no JSON for {url}.")
                            out.write(chunk)
                tmp.replace(path)
            except httpx.TransportError as exc:
                raise SourceUnavailable(f"Could not reach {DOWNLOAD_HOST}: {exc}") from exc
            finally:
                tmp.unlink(missing_ok=True)
                self._last = time.monotonic()


async def ingest(catalogue: CatalogueClient, index: VestnikIndex, issue: dict, *, keep_raw: bool) -> int:
    """Download, parse and store one issue; returns its number of records."""
    path = raw_path(issue["year"], issue["number"])
    fresh = path.exists() and path.stat().st_mtime >= _timestamp(issue["modified"])
    if not fresh:
        await catalogue.download_to(issue["url"], path)
    meta, notices = vestnik.parse_issue(gzip.decompress(path.read_bytes()))
    titled, holds = (issue["year"], issue["number"]), (meta["year"], meta["number"])
    if holds != titled and holds in index.issue_state():
        # The portal has uploads that repeat a neighbouring issue under another title.
        path.unlink()
        raise DuplicateUpload(
            f"the dataset titled {titled[1]}/{titled[0]} holds issue {holds[1]}/{holds[0]} again (a duplicate upload); "
            f"{titled[1]}/{titled[0]} itself is not on {DOWNLOAD_HOST}"
        )
    if not keep_raw and not fresh:
        path.unlink()
    index.add_issue(meta, notices, dataset=issue["dataset"], modified=issue["modified"])
    for problem in meta.get("skipped", []):
        log.warning("%s Vestník %s/%s: skipped %s", _now(), issue["number"], issue["year"], problem)
    return len(notices)


def _timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def pending(listed: list[dict], state: dict[tuple[int, int], str | None]) -> list[dict]:
    """Issues not indexed yet, or uploaded again since they were."""
    out = []
    for issue in listed:
        key = (issue["year"], issue["number"])
        if key not in state or (issue["modified"] and (state[key] or "") < issue["modified"]):
            out.append(issue)
    return out


async def refresh(index: VestnikIndex, catalogue: CatalogueClient, *, limit: int = INLINE_LIMIT) -> dict | None:
    """Top up an index that was built before: fetch up to limit new or changed issues. Returns a
    note for the tool result when more are missing or the portal failed, else None."""
    last = index.get_meta("checked_at")
    if last and time.time() - float(last) < CHECK_EVERY:
        return None
    state = index.issue_state()
    newest = max(state.items(), key=lambda kv: kv[1] or "")[1] if state else None
    try:
        listed = await catalogue.list_issues(modified_since=newest[:10] if newest else None)
        # Issues newer than the index, or indexed ones uploaded again; older gaps are the CLI's job.
        todo = [i for i in pending(listed, state) if (i["year"], i["number"]) > max(state) or (i["year"], i["number"]) in state]
        for issue in todo[-limit:]:
            await ingest(catalogue, index, issue, keep_raw=index.get_meta("keep_raw") == "1")
    except (SourceUnavailable, BadLink) as exc:
        return {"vestnik_update": f"Could not update the Vestník index: {exc}"}
    index.set_meta("checked_at", str(time.time()))
    if len(todo) > limit:
        return {"vestnik_update": f"{len(todo) - limit} more new Vestník issues are not indexed yet; run {sync_command('uvo')}."}
    return None


class BackfillStopped(Exception):
    """A failure that would hit every further issue too: the network, the disk."""


async def backfill(
    since: str, keep_raw: bool, interval: float, *, reindex: bool = False, out=sys.stderr,
    index: VestnikIndex | None = None, catalogue: CatalogueClient | None = None,
) -> list[str]:
    """Fetch and index every missing issue since a date. A failing issue (bad link, HTTP error,
    unparseable file) is logged and skipped; the run stops only on errors that would repeat for
    every issue: a full disk, or MAX_NETWORK_FAILURES unreachable downloads in a row. Returns the
    failed issues."""
    index = index or VestnikIndex()
    catalogue = catalogue or CatalogueClient(interval=interval)
    index.set_meta("keep_raw", "1" if keep_raw else "0")
    failed: list[str] = []
    say = lambda text: print(f"{_now()} {text}", file=out, flush=True)  # noqa: E731
    try:
        # A dataset is uploaded on its issue's day or (for the first issues) a few weeks later.
        listed = [i for i in await catalogue.list_issues() if (i["issued"] or i["modified"] or "9") >= since]
        todo = listed if reindex else pending(listed, index.issue_state())
        say(f"{len(listed)} issues since {since}; {len(todo)} to fetch into {index.path}")
        started = time.monotonic()
        unreachable = 0
        for n, issue in enumerate(todo, 1):
            name = f"{issue['number']}/{issue['year']}"
            try:
                records = await ingest(catalogue, index, issue, keep_raw=keep_raw)
            except OSError as exc:  # disk full, permissions: every further issue would fail too
                raise BackfillStopped(f"{name}: {exc}") from exc
            except Exception as exc:  # noqa: BLE001  (log it and carry on with the next issue)
                failed.append(f"{name}: {exc}")
                say(f"[{n}/{len(todo)}] {name} FAILED: {exc}")
                unreachable = unreachable + 1 if isinstance(exc, SourceUnavailable) and "reach" in str(exc) else 0
                if unreachable >= MAX_NETWORK_FAILURES:
                    raise BackfillStopped(f"{unreachable} downloads in a row could not reach {DOWNLOAD_HOST}") from exc
                continue
            unreachable = 0
            left = (time.monotonic() - started) / n * (len(todo) - n)
            say(f"[{n}/{len(todo)}] {name}: {records} records, ~{left / 60:.0f} min left")
        index.set_meta("checked_at", str(time.time()))
        say(f"done: {index.coverage()}")
    except (BackfillStopped, SourceUnavailable) as exc:
        say(f"STOPPED: {exc}. Run again to continue; indexed issues are kept.")
    finally:
        if failed:
            say(f"{len(failed)} issues failed (run again to retry):")
            for line in failed:
                print(f"  {line}", file=out, flush=True)
        await catalogue.aclose()
        index.close()
    return failed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sk-public-money-sync uvo", description="Download Vestník verejného obstarávania issues into the local index.")
    parser.add_argument("--since", default=FIRST_ISSUE_DATE, help=f"first publication date to fetch, YYYY-MM-DD (default {FIRST_ISSUE_DATE})")
    parser.add_argument("--keep-raw", action=argparse.BooleanOptionalAction, default=True,
                        help="keep each issue gzipped (about 1 MB each) so the index can be rebuilt without downloading again")
    parser.add_argument("--interval", type=float, default=INTERVAL, help=f"seconds between downloads (default {INTERVAL})")
    parser.add_argument("--reindex", action="store_true", help="parse every issue again (from the kept raw files where present), e.g. after an update of this program")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(backfill(args.since, args.keep_raw, max(args.interval, 0.5), reindex=args.reindex))


if __name__ == "__main__":
    main()
