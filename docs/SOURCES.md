# Sources in detail

Reference notes on each register: what its tools return, how the server matches names and
identifiers, and each portal's quirks, rate limits and licence. For an overview and installation, see
the [README](../README.md).

## Sources and tools

### CRZ: Centrálny register zmlúv ([crz.gov.sk](https://www.crz.gov.sk))

Ministries, state bodies and their organisations must publish their contracts here before the contracts
take effect.

| Tool | What it does |
| --- | --- |
| `crz_search_contracts` | Searches by supplier, buyer, IČO, title, contract number, department, amount range and publication date, or by full text. Returns 20 results per page, sorted by newest or by amount. |
| `crz_get_contract` | Gets the full detail of a contract or amendment by CRZ ID: both parties with address and IČO, dates, amounts, attachments and amendments. |
| `crz_read_attachment` | Extracts the text of a PDF attachment, page by page. About 1 in 5 recent attachments is a scan without a text layer; the result says so. |
| `crz_daily_changes` | Lists every record published or changed on a past day, from the official daily export. Its text filter ignores case and diacritics. |
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
- Name searches ignore case and **diacritics** in every Slovak, Czech and Hungarian spelling: "Siroky" finds Široký, Siroký and Široky, and "ZDUTOVA - STASTNA" finds Zdútová - Šťastná.
  - RPVS itself compares strings exactly, so the server matches letter by letter (`s` or `š`, then `i` or `í`, …).
  - RPVS limits query complexity, so for long names only the first ~14 letters are checked on the server and the rest are verified locally. The `matching` count is then `null`.
  - `rpvs_search_partner` matches the *start* of a name with any diacritics. Anywhere else in the name it matches only as typed or without diacritics, because the API offers no accent-insensitive substring search.
- Short Slovak IČOs are zero-padded to 8 digits automatically. Foreign partners keep their own IDs.
- Results include the full birth date where RPVS has one, which helps tell namesakes apart.

### RPO: Register právnických osôb ([rpo.minv.sk](https://rpo.minv.sk))

The official register of every Slovak legal entity: companies, sole traders, public bodies,
associations, foundations. It merges the source registers (Obchodný register, Živnostenský register,
registers of associations and others) under one record per IČO. The Ministry of Interior runs it; the
API is still served from the Statistical Office's host.

| Tool | What it does |
| --- | --- |
| `rpo_get_organization` | Takes an IČO or RPO ID and returns names over time, seat, legal form and status, founding and dissolution dates, source register with court and file number, statutory body, stakeholders (partners, sole shareholder, supervisory board, procurists), capital and contributions, predecessors and successors. Optionally includes their full history. |
| `rpo_search_organization` | Finds organizations by a word of their current or former name, optionally in one town. |
| `rpo_search_person` | Finds organizations where a person is in the statutory body or a stakeholder, with each organization's IČO. |

Things to know:

- RPO publishes **no birth dates** and, since the law changed, no beneficial owners. Use RPVS for owners and birth dates.
- Searches ignore case and diacritics on the server (Oracle Text): "siroky" finds Široký. They match anywhere in the name, current or former; a hit found only by a former name says so in `matched_former_name`.
- `/search` returns at most 500 records and has no paging; `capped` says when that limit was hit.
- The API matches a person's first name and surname anywhere in the organization, possibly on two different people. `rpo_search_person` therefore checks each candidate's full record and keeps only real matches, which costs about a second per organization.
- A dissolved organization is shown as it was on the day before its dissolution (`shown_as_of`), since RPO ends its people and capital then.
- For foreign companies RPO stores no IČO ("Neuvedené") and a placeholder founding date of 1800-01-01; both come out as `null`.
- Data lags the source registers by up to a day.

### RÚZ: Register účtovných závierok ([registeruz.sk](https://www.registeruz.sk))

Companies, sole traders with double-entry books, non-profits and public bodies file their financial
statements (*účtovná závierka*) here. Each statement consists of reports (*výkazy*): balance sheet,
profit and loss, notes, auditor's report.

| Tool | What it does |
| --- | --- |
| `ruz_search_entity` | Finds accounting entities by name, IČO or DIČ. Returns at most 10 matches. |
| `ruz_get_entity` | Takes an IČO or RÚZ ID and returns the entity (address, legal form, SK NACE, ŠÚSR employee band, ownership) with every statement it filed: period, individual or consolidated, filing and approval dates. |
| `ruz_get_financials` | Reads key figures per year: `revenue` (tržby), `total_income` (all výnosy or príjmy), `net_profit`, `total_assets` and `equity`. Each figure names the report row and column it came from, and anything that can't be read is listed under `not_mapped` with the reason. Report documents (PDFs, attachments) are linked. |

Things to know:

- Templates differ in their columns: a company's balance sheet has Brutto, Korekcia and Netto for the current year, a sole trader's lists the previous year first, and a single-entry non-profit splits income into taxed and untaxed activity without a total. The current-year column is therefore picked from the header texts, never by position, and rows are found by their labels. Summed columns are labelled as such.
- Mapped templates: double-entry businesses (Úč POD, also the 2009–2013 versions), micro entities (Úč MUJ), double-entry non-profits (Úč NUJ), single-entry non-profits (Úč NO), single-entry sole traders (Úč FO) and public administration (Úč ROPO SFOV, e.g. municipalities). Banks, insurers and funds have their own templates and come out as `not_mapped`.
- In single-entry bookkeeping, `net_profit` is income minus expenses (cash), and `equity` is assets minus liabilities.
- Companies reporting under **IFRS**, including most large and listed ones such as Slovnaft, file only PDFs. Their figures are `not_mapped` and the PDFs are linked.
- Statements contain no employee count. `employees` is ŠÚSR's current size band on the entity, not a per-year figure.
- Municipalities and groups also file consolidated statements; `ruz_get_financials` reads individual ones unless `consolidated` is set.
- Name search uses the portal's suggestion list: it ignores case and diacritics and matches word starts ("szoke" finds Szőke and Szőkeová), but stops at 10 results.
- One IČO can have several entity records; deleted duplicates are skipped.
- Amounts are in EUR as filed, whole euros or with cents depending on the filer.

### ÚVO: verejné obstarávanie (public procurement)

Who bought what by which procedure, how many tenders came in, who won and for how much. Two sources:

- The **Vestník verejného obstarávania**, ÚVO's journal, published as [open data](https://data.slovensko.sk)
  one JSON file per issue. It has every notice since ÚVO moved to eForms in October 2023, also those
  below the EU thresholds, and the buyers' half-year reports of low-value contracts (*súhrnná správa*),
  each contract with supplier, value without VAT and CRZ link. It has no search API, so the issues
  are downloaded into a local index (see below).
- [TED](https://ted.europa.eu), the EU's procurement journal, where Slovak buyers publish the
  procurements above the EU thresholds; searched live, back to 2016.

A search reads the index for the dates it covers and TED for the years before, newest first. A TED
notice that is also in the index (the same eForms notice ID) is left out. Each result names its source.

| Tool | What it does |
| --- | --- |
| `uvo_search_procurements` | Searches by IČO as buyer or winner (`any`), or only one of them, or as tenderer (won or lost) or subcontractor; by words, CPV code (with the codes below it) and publication date; results only by default. Returns 20 per page with buyer and winners (IČO), procedure type, estimated and final value, tenders received, contract numbers and CRZ IDs. |
| `uvo_get_notice` | Gets one notice in full: buyers with contacts and buyer profile, and per lot the result: tenders received by type, lowest and highest bid, each tender with its tenderers (consortium members, subcontractors), amount and whether it won, and the signed contracts with their CRZ ID for `crz_get_contract`. A low-value report entry lists its contracts with suppliers and values. |

Building the Vestník index:

```sh
sk-public-money-sync uvo              # every issue since October 2023: ~770 downloads, ~7 GB, ~45 min
sk-public-money-sync uvo --since 2026-01-01
sk-public-money-sync uvo --reindex    # parse again from the kept files, e.g. after an update
```

Each issue is streamed straight into a gzip file; progress lines carry timestamps, so the output can
be appended to a log file.

The index takes about 300 MB in the cache directory; the downloaded issues are kept gzipped (about
1 MB each, `--no-keep-raw` to skip). The command can be stopped and run again; it fetches only what is
missing or was uploaded again. Once built, the tools top it up themselves: every 6 hours they check
for new issues and fetch up to 5. Without an index, the tools search TED only and say so.

Things to know:

- Notices name organisations by whatever the buyer typed as registration number: an IČO, a DIČ, an IČ DPH, or for foreign firms their own numbers. Each ID is labelled `ico`, `dic`, `ic_dph`, `foreign` or `unknown`; `ico` is `null` with an `ico_note` when there is no Slovak IČO. IČOs typed without leading zeros are padded and found either way.
- Values of framework agreements (*rámcová dohoda*) are the most the buyer may spend, not what was paid; such notices carry a note. A value of 0 means the buyer did not disclose it. Low-value reports give values without VAT.
- Vestník results say how the searched IČO appears in them (`matched_as`: buyer, winner, tenderer, subcontractor). Suppliers in low-value reports count as winners.
- TED indexes losing tenderers and subcontractors only in eForms notices (from November 2023); for older TED notices a tenderer search finds only wins, and the result says so.
- TED's search results flatten every field into lists. The search pairs winner names with IDs in the order TED lists them and leaves IDs unpaired when that doesn't line up; `uvo_get_notice` reads the notice itself.
- TED notices published before November 2023 use the older format, which records winners only in the text. A winner search finds them by full text and marks them `matched_by`, since the IČO could also be there in another role. Their detail lists only the winners and no contract links.
- Word search ignores case and diacritics. In the Vestník index, words of five or more letters also match other endings (*nafta* finds *nafty*); TED does its own matching.
- ÚVO's supervision notices in the Vestník (reviews of procedures) are not indexed.
- Corrected notices and reports ("Oprava: …") are indexed as their own records next to the original, labelled in `kind`; they are not merged into it.
- Three issues are missing from the index because data.slovensko.sk lacks them: 202/2023 has no usable download link, and the datasets titled 144/2024 and 145/2025 are byte-identical copies of 145/2024 and 144/2025. The sync reports them on every run. All other issues since 201/2023 (17 October 2023) are indexed.

## Being a polite robot

Each source has its own throttle:

- **CRZ** publishes [rules for machine access](https://www.crz.gov.sk/stahovanie-udajov-z-crz/): by day (06:00–20:00 Bratislava time) at most one request every 2 seconds, at night fewer than 3 per second. Clients that go faster are blocked for up to an hour. CRZ also has maintenance windows, often in the evening between 20:00 and 23:59. During a window it answers with a bare 404 page, and the tools report the portal as unavailable.
- **RPVS** uses its [open-data OData API](https://rpvs.gov.sk/opendatav2/), which needs no key and publishes no limits. Requests are spaced 0.5 s apart anyway.
- **RPO** uses the [REST API documented by MV SR](https://rpo.minv.sk/rpo-api-doc.html) at `api.statistics.sk/rpo/v1`, which needs no key and publishes no limits. Requests are spaced 0.5 s apart anyway. A detail takes about a second, while searches take 5–30 s.
- **data.slovensko.sk** (Vestník issues) publishes no limits and its robots.txt allows everything. The sync fetches one issue at a time, 2 s apart. The data are licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) by ÚVO.
- **TED** uses its [Search API](https://docs.ted.europa.eu/api/latest/index.html), which needs no key and publishes no rate limit; requests are spaced 0.5 s apart. Notice XML is cached for good, since published notices never change (a correction is a new notice). TED data may be reused free of charge with attribution (Commission Decision 2011/833/EU). ÚVO's own website disallows robots in its robots.txt and is not used.
- **RÚZ** uses its [open API](https://www.registeruz.sk/cruz-public/home/api), which needs no key and publishes no limits; robots.txt only disallows the XLS exports. Requests are spaced 0.5 s apart. Every statement and report is a separate request, so the first look at a large filer takes 10–30 s. Statements and reports are then cached for 3 days, and report templates (which RÚZ never changes) for good. RÚZ data are licensed [CC0](https://creativecommons.org/publicdomain/zero/1.0/).

CRZ daily exports and attachments are cached in `~/.cache/sk-public-money`, or in
`$SK_PUBLIC_MONEY_CACHE_DIR` when it's set. CRZ export data is licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) by Úrad vlády SR, and RPO data under
CC BY 4.0 by MV SR.

People in these registers are often private individuals, and a matching name isn't proof that two
records are the same person.
