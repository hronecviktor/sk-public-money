# Development

For an overview and installation, see the [README](../README.md).

Work from a clone, with an editable install and the test dependencies (Python 3.11 or newer):

```sh
git clone https://github.com/hronecviktor/sk-public-money.git
cd sk-public-money
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

To run Claude against your working copy instead of the GitHub version:

```sh
claude mcp add sk-public-money-dev -- "$PWD/.venv/bin/sk-public-money-mcp"
```

Each source lives in `src/sk_public_money/sources/<source>/`:

- `client.py`: HTTP access and throttling, built on `http.ThrottledClient`
- `parse.py`: turning responses into readable records
- `tools.py`: the MCP tools, registered on the shared server in `app.py`

To add a source, create the same three files and import its `tools` module in `server.py`. A source
can have more modules where it needs them: ÚVO adds `vestnik.py` (the Vestník parser), `index.py`
(the local SQLite index) and `sync.py` (run as `sk-public-money-sync uvo`).

A source that needs a local index keeps it in the cache directory and uses `localdb.py`:
`db_path()`, `connect()` with a meta table, `is_due()`/`mark_done()` for refresh intervals, and
`ensure_free_space()` before a large download. `ThrottledClient._download()` streams a large file to
disk. Its `sync.py` has `main(argv)` and a line in the `SOURCES` table of `sync.py` at the package
root, so `sk-public-money-sync <source>` builds or tops up its index. `sk-public-money-sync all` runs
every source in its own process, four at once, slowest first (the reverse of that table, which goes
from the quickest first build to the slowest; `--jobs 1` runs them in table order in one process).
Keep the table ordered by first-build time and add a row to the README's first-run table. Sources
run side by side, so a source must not share a portal with another one's sync, and
`ensure_free_space()` counts the space other running syncs have claimed. Delete raw downloads once they
are indexed unless the source needs them to re-index.

The tests run offline:

- **CRZ:** pages and an export saved from crz.gov.sk, in `tests/crz/fixtures`.
- **RPVS:** a mocked OData API with fictional people.
- **RPO:** a mocked REST API with fictional organizations and people.
- **ÚVO:** fictional TED notices in both XML formats and a fictional Vestník issue in `tests/uvo/fixtures`, and mocked TED and data.slovensko.sk APIs.
- **REPLIK:** a mocked SOAP endpoint with fictional companies and people, XML shapes copied from real answers.
- **Obchodný vestník:** fictional notices in the portal's XML forms, a synthetic gzip dump streamed from a mocked server, and a mocked sync API.
- **Courts:** a mocked ISU API and Súdna rada list with fictional judges, companies and disqualified persons, and generated PDFs. The RPO tests mock the register of disqualified persons too, with the index in a temporary cache.
- **RÚZ:** real report templates in `tests/ruz/fixtures` with fictional entities and amounts, and a mocked API.
- **PPA:** workbooks built in the tests in both published layouts with fictional beneficiaries, the real download section of the PPA page in `tests/ppa/fixtures`, and a mocked RPO.
- **Party financing:** a mocked Slovenská sporiteľňa API with fictional accounts and payments, minv.sk listing pages with fictional parties, and report PDFs generated in the tests (text pages, a scanned page and a page with an invisible scanner OCR layer).
- **ITMS21+ (EU funds):** a mocked API with fictional organisations and projects in the shape of real records.

## Prebuilt index snapshots

Slow indexes chosen by the maintainer (`BUNDLED` in `bundle.py`: the Vestník, CC BY 4.0, and ITMS21+, whose licence is not published) are
published as xz-compressed SQLite snapshots in the GitHub release tagged `indexes`. A sync of such a
source whose index doesn't exist yet downloads the snapshot first, checks its SHA-256 and schema
version, and then fetches only what's newer; any problem falls back to a full build.

To publish fresh snapshots, build the indexes, then:

```sh
sk-public-money-sync bundle --out ../sk-public-money-bundle
gh release upload indexes ../sk-public-money-bundle/*.sqlite.xz --clobber   # data first
gh release upload indexes ../sk-public-money-bundle/manifest.json --clobber  # manifest last
```

The manifest names each file's hash, so a download during an upload fails the check and builds from
the source instead of using a mismatched file. Bump a source's `SCHEMA_VERSION` when an index built by
older code can't be topped up by newer code: clients then ignore older snapshots. Each bundled source
is a maintainer's decision: open data, or data that is mostly about organisations, such as ITMS21+
(recipients and suppliers, a few of them sole traders) whose licence is not published. Sources that are
mostly about private individuals or come with unclear terms of reuse (Finančná správa, PPA, the
register of disqualified persons, Obchodný vestník via Datahub) are not bundled.
