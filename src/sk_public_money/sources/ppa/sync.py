"""sk-public-money-sync ppa: download PPA's beneficiary lists into the local index.

PPA publishes the last two financial years (Article 98 of Regulation (EU) 2021/2116 keeps each list
public for two years), about 8 MB of XLSX each. A year is downloaded again only when its link
changes (a new upload). Years PPA no longer lists are dropped from the index, so it mirrors what is
published, unless --keep-delisted. Each XLSX is deleted once indexed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime

from ... import localdb
from ...http import SourceUnavailable, cache_dir
from . import parse
from .client import PPAClient
from .index import PPAIndex

DOWNLOAD_SIZE = 50 << 20  # room for one XLSX (about 9 MB) plus the index growing


def _say(out, text: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}", file=out, flush=True)


async def sync(*, reindex: bool = False, keep_delisted: bool = False, out=sys.stderr,
               index: PPAIndex | None = None, client: PPAClient | None = None) -> list[dict]:
    """Bring the index in line with the page; returns the years (re)indexed."""
    index = index or PPAIndex()
    client = client or PPAClient()
    done = []
    try:
        files = await client.list_files()
        have = index.years()
        _say(out, f"PPA lists financial years {', '.join(str(f['year']) for f in files)}; index at {index.path}")
        for f in files:
            if not reindex and f["year"] in have and have[f["year"]]["url"] == f["url"]:
                _say(out, f"FY{f['year']}: up to date")
                continue
            path = cache_dir() / "ppa" / f"fy{f['year']}.xlsx"
            localdb.ensure_free_space(path, DOWNLOAD_SIZE)
            started = time.monotonic()
            try:
                await client.download(f["url"], path)
                meta, beneficiaries = parse.read(path)
                if meta["fiscal_year"] != f["year"]:
                    raise parse.FormatError(f"the file listed as {f['year']} is titled financial year {meta['fiscal_year']}")
                stats = index.replace_year(f["year"], beneficiaries, url=f["url"], published=f["published"])
            finally:
                path.unlink(missing_ok=True)
            done.append(stats)
            _say(out, f"FY{f['year']}: {stats['beneficiaries']} beneficiaries, {stats['operations']} operations, "
                      f"EUR {stats['total']:,.2f} ({time.monotonic() - started:.0f} s)")
        if not keep_delisted:
            for year in set(have) - {f["year"] for f in files}:
                index.delete_year(year)
                _say(out, f"FY{year}: no longer published by PPA, removed from the index")
        index.set_meta("checked_at", str(time.time()))
    except (SourceUnavailable, parse.FormatError) as exc:
        _say(out, f"STOPPED: {exc}")
    finally:
        await client.aclose()
        index.close()
    return done


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sk-public-money-sync ppa",
                                     description="Download PPA's lists of CAP beneficiaries (EAGF and EAFRD) into the local index.")
    parser.add_argument("--reindex", action="store_true", help="download and index every listed year again")
    parser.add_argument("--keep-delisted", action="store_true", help="keep years PPA no longer publishes")
    args = parser.parse_args(argv)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(sync(reindex=args.reindex, keep_delisted=args.keep_delisted))


if __name__ == "__main__":
    main()
