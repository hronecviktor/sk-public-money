"""List the parties' annual reports from minv.sk into the local index: sk-public-money-sync party.

The commission's overview page (/?vyrocnespravyps) links one index page per year since 2015; each
index page links one PDF per party. Only these pages are fetched (about a dozen requests, 1.5 s
apart); the PDFs are downloaded later, one at a time, when party_read_report reads one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime

from . import parse
from .client import REPORTS_INDEX, REPORTS_OVERVIEW, MinvClient
from .index import ReportIndex

# The tools refresh the listing of the newest years inline when it is older than this.
REFRESH_EVERY = 7 * 24 * 3600
INLINE_YEARS = 2


async def list_years(client: MinvClient) -> dict[int, str]:
    """{year: index page URL}, from the overview page."""
    pages = parse.year_pages(await client.page(REPORTS_OVERVIEW))
    if not pages:
        raise LookupError("The minv.sk overview of annual reports links no year pages; its layout may have changed.")
    return pages


async def refresh(index: ReportIndex, client: MinvClient, *, years: list[int] | None = None, say=None) -> dict[int, dict]:
    """Fetch the listing of the given years (default: every year on the overview) into the index. A
    year the overview does not link yet is tried at the URL pattern used since 2019."""
    pages = await list_years(client)
    wanted = [y for y in sorted(pages) if years is None or y in years]
    missing = [y for y in (years or []) if y not in pages]
    for y in missing:
        pages[y] = REPORTS_INDEX.format(year=y)
        wanted.append(y)
    out = {}
    for year in sorted(set(wanted)):
        try:
            html = await client.page(pages[year])
        except LookupError:
            if year in missing:  # a year the overview does not link and that has no page yet
                continue
            raise
        listed = parse.report_links(html, year)
        if not listed:
            if say:
                say(f"{year}: no reports listed on {pages[year]}")
            continue
        out[year] = index.replace_year(year, pages[year], listed, parse.year_notes(html))
        if say:
            say(f"{year}: {len(listed)} reports ({out[year]['added']} new, {out[year]['dropped']} dropped)")
    index.set_meta("listed_at", str(time.time()))
    return out


def _years(spec: str) -> list[int]:
    first, _, last = spec.partition("-")
    return list(range(int(first), int(last or first) + 1))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="sk-public-money-sync party",
        description="List the political parties' annual reports (výročné správy) published on minv.sk.",
    )
    parser.add_argument("--years", help="only these years, e.g. 2024 or 2020-2025 (default: every listed year)")
    args = parser.parse_args(argv)
    say = lambda text: print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}", file=sys.stderr, flush=True)  # noqa: E731

    async def run() -> None:
        index, client = ReportIndex(), MinvClient()
        try:
            await refresh(index, client, years=_years(args.years) if args.years else None, say=say)
            total = sum(y["reports"] for y in index.years().values())
            say(f"done: {total} reports in {index.path}")
        finally:
            await client.aclose()
            index.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
