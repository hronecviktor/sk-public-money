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
root, so `sk-public-money-sync <source>` builds or tops up its index. Delete raw downloads once they
are indexed unless the source needs them to re-index.

The tests run offline:

- **CRZ:** pages and an export saved from crz.gov.sk, in `tests/crz/fixtures`.
- **RPVS:** a mocked OData API with fictional people.
- **RPO:** a mocked REST API with fictional organizations and people.
- **ÚVO:** fictional TED notices in both XML formats and a fictional Vestník issue in `tests/uvo/fixtures`, and mocked TED and data.slovensko.sk APIs.
- **REPLIK:** a mocked SOAP endpoint with fictional companies and people, XML shapes copied from real answers.
- **Obchodný vestník:** fictional notices in the portal's XML forms, a synthetic gzip dump streamed from a mocked server, and a mocked sync API.
- **RÚZ:** real report templates in `tests/ruz/fixtures` with fictional entities and amounts, and a mocked API.
