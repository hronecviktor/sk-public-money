"""Download Vestník issues from data.slovensko.sk into the local index.

The national open-data catalogue lists one dataset per issue ("Vestník 207/2026", publisher ÚVO,
IČO 31797903) with a JSON download. Its SPARQL endpoint finds them; data.slovensko.sk publishes no
rate limit and its robots.txt allows everything, so issues are fetched one at a time with a pause.
JSON issues start with 201/2023 (17 October 2023), when ÚVO moved to eForms.

The full backfill (about 770 issues, 7 GB) runs from the command line: sk-public-money-uvo-sync.
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
            issue = {
                "year": key[0], "number": key[1], "dataset": b["dataset"]["value"], "url": b["url"]["value"],
                "issued": (b.get("issued") or {}).get("value"),
                "modified": (b.get("modified") or b.get("issued") or {}).get("value"),
            }
            # An issue re-uploaded as a second distribution: keep the newest.
            if key not in issues or (issue["modified"] or "") > (issues[key]["modified"] or ""):
                issues[key] = issue
        return [issues[k] for k in sorted(issues)]

    async def download(self, url: str) -> bytes:
        if not url.startswith(f"https://{DOWNLOAD_HOST}/"):
            raise ValueError(f"Unexpected download URL: {url}")
        resp = await self._get(url)
        if resp.status_code != 200 or not resp.content.lstrip().startswith(b"{"):
            raise SourceUnavailable(f"data.slovensko.sk returned HTTP {resp.status_code} for {url}.")
        return resp.content


async def ingest(catalogue: CatalogueClient, index: VestnikIndex, issue: dict, *, keep_raw: bool) -> int:
    """Download, parse and store one issue; returns its number of records."""
    path = raw_path(issue["year"], issue["number"])
    if path.exists() and path.stat().st_mtime >= _timestamp(issue["modified"]):
        data = gzip.decompress(path.read_bytes())
    else:
        data = await catalogue.download(issue["url"])
        if keep_raw:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(gzip.compress(data, 6))
            tmp.replace(path)
    meta, notices = vestnik.parse_issue(data)
    index.add_issue(meta, notices, dataset=issue["dataset"], modified=issue["modified"])
    for problem in meta.get("skipped", []):
        log.warning("Vestník %s/%s: skipped %s", issue["number"], issue["year"], problem)
    return len(notices)


def _timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


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
    except SourceUnavailable as exc:
        return {"vestnik_update": f"Could not check for new Vestník issues: {exc}"}
    index.set_meta("checked_at", str(time.time()))
    if len(todo) > limit:
        return {"vestnik_update": f"{len(todo) - limit} more new Vestník issues are not indexed yet; run sk-public-money-uvo-sync."}
    return None


async def backfill(since: str, keep_raw: bool, interval: float, *, reindex: bool = False, out=sys.stderr) -> None:
    index = VestnikIndex()
    catalogue = CatalogueClient(interval=interval)
    index.set_meta("keep_raw", "1" if keep_raw else "0")
    try:
        # A dataset is uploaded on its issue's day or (for the first issues) a few weeks later.
        listed = [i for i in await catalogue.list_issues() if (i["issued"] or i["modified"] or "9") >= since]
        todo = listed if reindex else pending(listed, index.issue_state())
        print(f"{len(listed)} issues since {since}; {len(todo)} to fetch into {index.path}", file=out)
        started = time.monotonic()
        for n, issue in enumerate(todo, 1):
            try:
                records = await ingest(catalogue, index, issue, keep_raw=keep_raw)
            except SourceUnavailable as exc:
                print(f"  {issue['number']}/{issue['year']}: {exc}; skipped, run again to retry", file=out)
                continue
            left = (time.monotonic() - started) / n * (len(todo) - n)
            print(f"  [{n}/{len(todo)}] {issue['number']}/{issue['year']}: {records} records, ~{left / 60:.0f} min left", file=out)
        index.set_meta("checked_at", str(time.time()))
        print(f"done: {index.coverage()}", file=out)
    finally:
        await catalogue.aclose()
        index.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Vestník verejného obstarávania issues into the local index.")
    parser.add_argument("--since", default=FIRST_ISSUE_DATE, help=f"first publication date to fetch, YYYY-MM-DD (default {FIRST_ISSUE_DATE})")
    parser.add_argument("--keep-raw", action=argparse.BooleanOptionalAction, default=True,
                        help="keep each issue gzipped (about 1 MB each) so the index can be rebuilt without downloading again")
    parser.add_argument("--interval", type=float, default=INTERVAL, help=f"seconds between downloads (default {INTERVAL})")
    parser.add_argument("--reindex", action="store_true", help="parse every issue again (from the kept raw files where present), e.g. after an update of this program")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(backfill(args.since, args.keep_raw, max(args.interval, 0.5), reindex=args.reindex))


if __name__ == "__main__":
    main()
