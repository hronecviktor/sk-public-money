# sk-public-money

An [MCP](https://modelcontextprotocol.io) server for Slovak public-money registers. It lets an AI
assistant follow money across registers: find a company's contracts with the state, read them, and see
who owns the company.

Companies are identified by **IČO** in every register, so one tool's result feeds the next. For
example, a person turns up as a beneficial owner in RPVS (`rpvs_search_person`), and that company's
IČO then finds its state contracts in CRZ (`crz_search_contracts`).

## Sources and tools

### CRZ: Centrálny register zmlúv ([crz.gov.sk](https://www.crz.gov.sk))

Ministries, state bodies and their organisations must publish their contracts here before the contracts
take effect.

| Tool | What it does |
| --- | --- |
| `crz_search_contracts` | Searches by supplier, buyer, IČO, title, contract number, department, amount range and publication date, or by full text. Returns 20 results per page, sorted by newest or by amount. |
| `crz_get_contract` | Gets the full detail of a contract or amendment by CRZ ID: both parties with address and IČO, dates, amounts, attachments and amendments. |
| `crz_read_attachment` | Extracts the text of a PDF attachment, page by page. About 1 in 5 recent attachments is a scan without a text layer; the result says so. |
| `crz_daily_changes` | Lists every record published or changed on a past day, from the official daily export. |
| `crz_list_departments` | Lists the department (rezort) IDs that the search filter takes. |

Things to know:

- Full-text search can't be combined with the field filters, because the portal ignores them. Full-text search also matches any word, so it's poor at finding people; use the supplier or buyer name filter instead.
- In the portal's terms, "buyer" is the *objednávateľ* (the public body) and "supplier" is the *dodávateľ*.
- CRZ's "text"/"sken" labels on attachments don't tell you whether a PDF has a text layer.

### RPVS: Register partnerov verejného sektora ([rpvs.gov.sk](https://rpvs.gov.sk/rpvs))

Companies and people that receive public money above legal thresholds must register here with their
beneficial owners (*konečný užívateľ výhod*, KUV). An authorized person, usually a law firm, verifies
the owners.

| Tool | What it does |
| --- | --- |
| `rpvs_get_partner` | Takes an IČO or partner ID and returns the partner's beneficial owners, authorized person and public officials, plus any deletion, fine or qualified complaint. Optionally includes their full history. |
| `rpvs_search_partner` | Finds partners by part of their name. |
| `rpvs_search_person` | Finds where a person is a beneficial owner, public official or authorized person, with each partner's IČO. |

Things to know:

- When a partner, or a company that owns it, is listed on a stock exchange, or when no person owns 25 %, RPVS lists **top management** as the beneficial owners. For example, Slovnaft's "owners" are its executives, not MOL's shareholders.
- RPVS name filters are case-insensitive but **diacritics must match**: "Horváth" doesn't find "Horvath". (CRZ ignores diacritics.)
- Short Slovak IČOs are zero-padded to 8 digits automatically. Foreign partners keep their own IDs.
- The RPVS API returns full birth dates; this server returns only the **birth year**.

## Being a polite robot

Each source has its own throttle:

- **CRZ** publishes [rules for machine access](https://www.crz.gov.sk/stahovanie-udajov-z-crz/): by day (06:00–20:00 Bratislava time) at most one request every 2 seconds, at night fewer than 3 per second. Clients that go faster are blocked for up to an hour. CRZ also has maintenance windows, often in the evening between 20:00 and 23:59. During a window it answers with a bare 404 page, and the tools report the portal as unavailable.
- **RPVS** uses its [open-data OData API](https://rpvs.gov.sk/opendatav2/), which needs no key and publishes no limits. Requests are spaced 0.5 s apart anyway.

CRZ daily exports and attachments are cached in `~/.cache/sk-public-money`, or in
`$SK_PUBLIC_MONEY_CACHE_DIR` when it's set. CRZ export data is licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) by Úrad vlády SR.

People in these registers are often private individuals, and a matching name isn't proof that two
records are the same person.

## Install

Requires Python 3.11 or newer.

```sh
python3 -m venv ~/envs/sk-public-money
~/envs/sk-public-money/bin/pip install -e '.[dev]'
```

### Claude Code

```sh
claude mcp add sk-public-money -- ~/envs/sk-public-money/bin/sk-public-money-mcp
```

### Claude Desktop and other clients

```json
{
  "mcpServers": {
    "sk-public-money": { "command": "/home/you/envs/sk-public-money/bin/sk-public-money-mcp" }
  }
}
```

The server speaks MCP over stdio.

## Development

```sh
~/envs/sk-public-money/bin/pytest
```

Each source lives in `src/sk_public_money/sources/<source>/`:

- `client.py`: HTTP access and throttling, built on `http.ThrottledClient`
- `parse.py`: turning responses into readable records
- `tools.py`: the MCP tools, registered on the shared server in `app.py`

To add a source, create the same three files and import its `tools` module in `server.py`.

The tests run offline:

- **CRZ:** pages and an export saved from crz.gov.sk, in `tests/crz/fixtures`.
- **RPVS:** a mocked OData API with fictional people.
