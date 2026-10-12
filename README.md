# crz-mcp

An [MCP](https://modelcontextprotocol.io) server for the Slovak **Central Register of Contracts**
([Centrálny register zmlúv, crz.gov.sk](https://www.crz.gov.sk)). Slovak ministries, state bodies and
their organisations must publish their contracts there before the contracts take effect. With this
server an AI assistant can search the register, read contract details and attachments, and go through
each day's new and changed contracts.

## Tools

| Tool | What it does |
| --- | --- |
| `search_contracts` | Searches the live register by supplier, buyer, IČO, title, contract number, department, amount range and publication date, or by full text. Returns 20 results per page, sorted by newest or by amount. |
| `get_contract` | Gets the full detail of a contract or amendment by CRZ ID: both parties with address and IČO, department, dates, amounts, attachments, and the contract's amendments (dodatky). |
| `read_attachment` | Extracts the text of a PDF attachment, page by page. Scanned PDFs have no text layer, and the result says so. |
| `daily_changes` | Lists every record published or changed on a past day, from the official daily export. Filters: text, IČO, department. |
| `list_departments` | Lists the departments (rezorty) with the IDs that the `department_id` filter takes. |

Things to know:

- Full-text search can't be combined with the field filters, because the portal ignores filters when it gets a full-text query. The tool refuses that combination.
- In the portal's terms, "buyer" is the *objednávateľ* (the public body) and "supplier" is the *dodávateľ*.
- `daily_changes` also lists old contracts that were edited that day. Check `dates.published` to pick out new ones.

## Being a polite robot

CRZ publishes [rules for machine access](https://www.crz.gov.sk/stahovanie-udajov-z-crz/):

- by day (06:00–20:00 Bratislava time), at most one request every 2 seconds
- at night, fewer than 3 requests per second

Clients that go faster are blocked for up to an hour. This server sends every request through one
throttle that follows these rules, so a call can take a few seconds. Narrow searches beat paging
through hundreds of results.

For bulk work, CRZ recommends the daily export ZIPs (`https://www.crz.gov.sk/export/YYYY-MM-DD.zip`),
which `daily_changes` reads. Downloaded exports and attachments are cached in `~/.cache/crz-mcp`, or in
`$CRZ_MCP_CACHE_DIR` when it's set. The export data is licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) by Úrad vlády SR, so credit the source when you
reuse it.

The portal has maintenance windows, announced on the site, that are often in the evening between 20:00
and 23:59. During a window it answers with a bare 404 page, and the tools then report that the portal
is unavailable.

## Install

Requires Python 3.11 or newer.

```sh
python3 -m venv ~/envs/crz-mcp
~/envs/crz-mcp/bin/pip install -e '.[dev]'
```

### Claude Code

```sh
claude mcp add crz -- ~/envs/crz-mcp/bin/crz-mcp
```

### Claude Desktop and other clients

```json
{
  "mcpServers": {
    "crz": { "command": "/home/you/envs/crz-mcp/bin/crz-mcp" }
  }
}
```

The server speaks MCP over stdio.

## Development

```sh
~/envs/crz-mcp/bin/pytest
```

The tests run offline against pages and an export saved from crz.gov.sk in `tests/fixtures`. If
the portal's markup changes, save fresh copies of those pages and update the parsers in
`src/crz_mcp/parse.py`.
