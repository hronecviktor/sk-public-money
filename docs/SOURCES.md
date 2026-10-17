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

### Finančná správa SR: the tax authority ([opendata.financnasprava.sk](https://opendata.financnasprava.sk))

The tax authority publishes lists of taxpayers as zipped XML files at
`https://report.financnasprava.sk/<list>.zip`, no key needed. They have no search API, so
`sk-public-money-sync fs` downloads them into a local index and deletes the zips. The lists used:

| List | What it holds | Updated |
| --- | --- | --- |
| `ds_dphs` | VAT register: IČ DPH, kind of registration (§ 4 payer, § 4b VAT group, § 5 foreign person, § 7 / § 7a registered but not a payer), registration date, payer from, date of a change of kind | daily |
| `ds_iz_ran` | Index daňovej spoľahlivosti: vysoko spoľahlivý, spoľahlivý, menej spoľahlivý, by DIČ | daily |
| `ds_dphz` | VAT payers with reasons to cancel their registration (§ 52(6) daňového poriadku), with the year of the breach and publication date | daily |
| `ds_dphv` | VAT payers struck off (§ 52(8)), with the date struck off | daily |
| `ds_dsdd` | Tax debtors: name, address, amount owed. **No IČO or DIČ.** | monthly |
| `ds_dppos` | Corporate income tax per tax period: tax assessed, additional tax assessed, tax loss | quarterly |
| `ds_dphno` | VAT excess deduction (nadmerný odpočet) or own tax liability per VAT period | quarterly |

| Tool | What it does |
| --- | --- |
| `fs_get_taxpayer` | Everything on one taxpayer by IČO (or DIČ, IČ DPH): VAT registrations, reliability index, cancellation and struck-off entries, corporate income tax per period (with the list it came from), the latest 12 VAT periods, the VAT group it represents (matched by the group's name), and debtor list entries with exactly the same name (`matched_by: name`). |
| `fs_search_debtors` | Searches the debtor list by words of the name (word starts, no case or diacritics), optionally by place and minimum debt; largest debts first; every result `matched_by: name`. |
| `fs_list_flagged` | Lists struck-off VAT payers (by date struck off), VAT payers with reasons to cancel (by publication date), or taxpayers rated menej spoľahlivý (by name), optionally by place. |
### Courts: decisions and judges ([obcan.justice.sk](https://obcan.justice.sk), [sudnarada.gov.sk](https://www.sudnarada.gov.sk))

The Ministry of Justice publishes every decision of the general courts (*rozhodnutie*: judgments,
orders, payment orders; about 4.7 million since around 2000) and its register of judges through the
ISU ("Infosúd") API, which is labelled *pilot*. The Judicial Council (Súdna rada) publishes judges'
yearly asset declarations (*majetkové priznania*, 2013 onwards, about 13,000 PDFs).

| Tool | What it does |
| --- | --- |
| `court_search_decisions` | Finds decisions by IČO or words, court, judge, legal area (*oblasť* or *podoblasť*) and date, newest first. Each hit has court, judge, case number, form and text snippets with the match marked «like this», plus counts by area, form and court type. |
| `court_get_decision` | A decision by guid or ECLI: nature (*povaha*: confirmed, overturned, …), legal area, cited laws with Slov-Lex links and the PDF link. With `text=true` it downloads the PDF and returns its text page by page. |
| `court_judge` | Finds judges by name (court, function, status, `judge_id` for the decision search) and lists their asset declarations from Súdna rada. With `declaration_url` it reads one declaration: real estate, movables, income from the office, debts and declared conflicts. |

Things to know:

- The API has no full text of decisions, only search snippets. The text comes from the PDF, which robots.txt of obcan.justice.sk disallows (`/content`); the user decided PDFs may be fetched one at a time on demand. Decisions from 2009 and declarations from 2017 checked so far have a text layer; a PDF without one is reported with a note.
- Decisions anonymise people (*A. B., nar. XX.X.XXXX*); companies keep their name and IČO.
- An IČO search is a full-text search, run twice: for `31322832` and for the phrase `"31 322 832"` (leading zeros dropped, so `"604 381"` also finds `00 604 381`). The results are merged, and `matching` gives the count for each spelling. The number may belong to another party, or be an amount, so the result says `matched_by` and each hit carries its snippet. Merged IČO results can be paged through the newest 500 only.
- Text search stems words and requires all of them; quotes make a phrase. Operators like `OR` are not supported (the backend fails on them, reported as a bad query).
- Judges' asset declarations are listed on Súdna rada's website, which matches a single surname without diacritics. The tool searches by the last word of the name first, then the others, and keeps declarations whose name contains every word given. They are matched to judges **by name only** (`asset_declarations_matched_by`), and list the court where the judge served that year.
- Not used: court hearings (*pojednávania*, no parties), and the register of disqualified persons (*diskvalifikácia*, people banned from company boards), which the API also offers.

### PPA: Pôdohospodárska platobná agentúra (farm subsidies)

Who received money from the Common Agricultural Policy: the EAGF (*EPZF*: direct payments, market
measures) and the EAFRD (*EPFRV*: rural development) with its national co-financing. PPA publishes
one list per EU financial year (16 October to 15 October; FY2025 = payments from 16 October 2024 to
15 October 2025) as XLSX on
[its website](https://www.apa.sk/o-nas/zverejnovane-udaje/prehlad-prijimatelia-pomoci-z-epzf-epfrv),
under Article 98 of Regulation (EU) 2021/2116, which keeps each list public for two years. The lists
have no search and change their download link with every upload, so they are kept in a local index.

| Tool | What it does |
| --- | --- |
| `ppa_search_beneficiary` | Finds beneficiaries by name (also by the name of a parent company), or by IČO: the company's current and former names are looked up in RPO and beneficiaries with exactly such a name are returned, with `seat_obec_matches` comparing the obec to the seat in RPO. Each result is one beneficiary in one financial year: total, EPZF, EPFRV and co-financing, the obce, the parent company, and the amounts by measure code with the CAP specific objectives. |
| `ppa_top` | Ranks beneficiaries of a financial year (default the newest) by what they received, overall or only under a measure code (`I.6`, or a group such as `V`), in an obec, or of one kind (natural person, entity, anonymised), with the number of beneficiaries and their sum. |

Building the index:

```sh
sk-public-money-sync fs                       # all lists; corporate tax archive since 2023
sk-public-money-sync fs --since 2018-01-01    # add the corporate tax archive back to 2018
sk-public-money-sync fs --lists ds_dphs,ds_iz_ran --force
```

The first run downloads about 170 MB and takes about 2–3 minutes; the index is about 220 MB.
Each further run asks each file's Last-Modified and downloads only changed lists. The tools check the
two small daily VAT flag lists every 6 hours and update them inline; they name any other list that is
older than usual and suggest the sync command.

Things to know:

- **Corporate income tax builds up from quarterly files.** The current `ds_dppos.zip` holds the returns processed in the latest quarter (mostly the previous tax year, plus late returns for older years); older quarters are in an [archive](https://www.financnasprava.sk/sk/elektronicke-sluzby/verejne-sluzby/zoznamy/archiv-vybranych-informacnych/zoznam-subjektov-s-vyskou-dane) back to 2018. The index keeps every published row with the date of its list (`list_of`); a period that appears in several lists (e.g. a later row with only the additional tax after a supplementary return) shows each row as published. A missing year may be in an older file (`--since`) or was never published.
- **VAT returns are only the latest quarter's file**: returns filed in that quarter, with late returns for older periods. Periods read `2026-03` (month) or `2026-Q1` (quarter).
- **VAT groups:** a company in a VAT group (e.g. Slovnaft) is not in the VAT register itself; the group is, named "Skupina DPH zastupovaná: <representative>", and files the VAT returns. `fs_get_taxpayer` links the group by that name for its representative. The lists do not name the other members.
- **The debtor list has no identifiers.** Matches are by name only; compare the address. `fs_get_taxpayer` shows debtor entries only when the cleaned name is identical.
- Names are cleaned of padding inside their quotes (`" CCL s.r.o. "` becomes `"CCL s.r.o."`); the quotes stay. Dates are converted from DD.MM.YYYY and DDMMYYYY. Some IČOs have 12 digits (organisational units: IČO + 4 digits) and are kept whole. A handful of violation years are nonsense (0, 25, 525) and are passed on as published text. Archive files up to 2023 are Windows-1250, newer ones UTF-8.
- Natural persons (sole traders, people registered for VAT, debtors) appear with name and address as published.
- Data: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), "Zdroj údajov: Finančná správa SR - https://opendata.financnasprava.sk/"; the reliability index file says CC0.
sk-public-money-sync ppa                  # both listed years: ~16 MB download, ~20 s, ~20 MB index
sk-public-money-sync ppa --reindex        # download and index every listed year again
sk-public-money-sync ppa --keep-delisted  # keep years PPA no longer publishes
```

A year is downloaded again only when its link changes, and each XLSX is deleted once indexed. Years
PPA stops publishing are dropped from the index, so it holds what PPA publishes. The tools say when
the index was last checked more than 45 days ago.

Things to know:

- **No IČO.** The lists give a name, for people a first name and surname, the obec, and for members of a group the parent company with its VAT or tax number as typed. `ico` is filled only when you search by IČO, and then only because the name matches (`matched_by: name`). Many sole traders appear under their own name without a surname column (`kind: entity`).
- **Anonymised recipients.** Recipients of EUR 1,250 or less a year are published as a numeric code (`kind: anonymised`). Where a small obec would identify a person, PPA gives the okres (FY2024: "Okres Komárno"); the `obec` filters match both.
- **Per operation rows.** Each beneficiary's operations are followed by a subtotal row; the index sums the operations and checks them against the subtotal (both years match to the cent: FY2025 16,589 beneficiaries, EUR 1,105,157,155.72; FY2024 16,037, EUR 708,644,192.44). Recoveries are negative amounts.
- **Measure codes** follow Annex IX of Implementing Regulation (EU) 2022/128: I.x direct payments under the CAP Strategic Plan, II.x direct payments under the old rules, III.x sectoral interventions, IV.x market measures, V.x rural development 2023–2027, VI.x the 2014–2022 Rural Development Programme. Some operations have no code ("(prázdne)"; FY2025: 266 operations, EUR 64.1 million), and FY2024 uses II.11 (6,584 operations, net EUR −2.7 million, mostly recoveries), which the 2024 version of Annex IX does not list; both are shown without a label.
- **Layouts differ by year.** FY2024 has (always empty) start and end date columns and repeats the name on the subtotal row; FY2025's sheet is titled "FY 2024" and joins a person's name on the subtotal row. Columns are found by their header and the year by the title row. A beneficiary whose name is spelt differently across its operations is shown under the subtotal's spelling with the others in `also_named`.
- PPA's older search for FY2021–2022 (archiv.apa.sk) is not used.
- No licence is stated for the lists. www.apa.sk has no robots.txt and publishes no limits; the sync makes three requests, 2 s apart.

### EU funds 2021–2027: ITMS21+ ([itms21.sk](https://www.itms21.sk))

ITMS21+ is the monitoring system of EU funds in Slovakia for the 2021–2027 period. Its
[open-data API](https://api.itms21.sk/public/v1/openapi.json) covers seven programmes: Program
Slovensko (PSK, ERDF/CF/JTF/ESF+), fisheries (PRH), Interreg VI-A Slovakia–Austria and
Slovakia–Czechia, and the home-affairs funds AMIF, ISF and BMVI. It has projects, grant applications
(*žiadosť o NFP*, ŽoNFP), payment claims (*žiadosť o platbu*, ŽoP), the procurements recipients ran for
their projects with the signed contracts, and the invoices and other documents claimed in projects.

Not covered: projects of 2014–2020 (ITMS2014+, whose open-data API at opendata.itms2014.sk refuses
this client with HTTP 403) and the Recovery and Resilience Plan (*Plán obnovy*), which has its own
system.

The API filters projects and claims only by its own subject IDs and has no supplier filter for
procurements, so its lists are kept in a local index:

| Tool | What it does |
| --- | --- |
| `eu_search_projects` | Searches projects (or grant applications, or both) by IČO as recipient (for applications the applicant) or partner, by words in code, name or organisation names, and by programme. Returns 20 per page with recipient and partners (IČO), status, contracted total, dates, the CRZ link of the funding contract, and the payment claims filed with the amounts claimed. |
| `eu_get_project` | Reads one project live: recipient, partners and provider (IČO), programme, call, amounts contracted, paid to the recipient and by funding source, dates, description, indicators, amendments, the project's suppliers (IČO), its invoices summed by supplier, and from the index its procurements with contracts and main suppliers and its payment claims. |
| `eu_search_procurements` | Searches procurements by IČO as buyer or as main supplier of a contract, by words (including buyer and supplier names) or by project, newest first: buyer, status, estimated value, procedure, CPV, tenders received, Vestník reference, projects, contracts with number, date, amount and main supplier. With a supplier IČO, it also sums the invoices that supplier billed to EU projects, by recipient. |

Building the index:

```sh
sk-public-money-sync eu                    # lists (~5 min), then procurement details and suppliers
sk-public-money-sync eu --no-details       # only reload the lists
sk-public-money-sync eu --max-details 2000 # spread the first detail pass over several runs
```

Every run reloads the lists in full (about 150 requests): the API documents a `modifiedSince` filter
but ignores it. Procurement details then come one request each, since only the detail links a
procurement to its projects and contracts: about 13,000 on the first run (some 4 hours), then those
that are new or changed, plus at most 300 re-checks per run of details older than 30 days (open
procurements) or 180 days (finished ones), since a contract can be added without the procurement's
change date moving. A weekly run thus makes a few hundred detail requests. Suppliers and some
buyers are records of a separate register with no list (`dodavatelobstaravatel`), so each new one is
fetched once and cached: about 4,000 on the first run. The index takes about 60 MB. The tools do not
top it up themselves; they say how old it is.

Things to know:

- Organisations are matched by IČO. Suppliers are matched by the main supplier of each contract only
  (*hlavný dodávateľ*); other members of a consortium are not indexed.
- Payment claims show the amount the recipient claimed (*nárokovaná suma*) and whether the claim was
  paid; how much was approved is not in the list. `eu_get_project` gives the total paid to the
  recipient (*poskytnuté prostriedky*).
- Invoices: about 75,000, each with its owner (the recipient), supplier and amount. The list does not
  say which project an invoice belongs to; `eu_get_project` reads a project's invoices live. Internal
  documents (flat rates, payroll) name the recipient itself as the supplier and are left out of the
  supplier sums.
- Natural persons (sole traders, individual recipients) are returned as the register publishes them.
- No licence or rate limit is published for the API. Requests are spaced 1 s apart.

## Being a polite robot

Each source has its own throttle:

- **CRZ** publishes [rules for machine access](https://www.crz.gov.sk/stahovanie-udajov-z-crz/): by day (06:00–20:00 Bratislava time) at most one request every 2 seconds, at night fewer than 3 per second. Clients that go faster are blocked for up to an hour. CRZ also has maintenance windows, often in the evening between 20:00 and 23:59. During a window it answers with a bare 404 page, and the tools report the portal as unavailable.
- **RPVS** uses its [open-data OData API](https://rpvs.gov.sk/opendatav2/), which needs no key and publishes no limits. Requests are spaced 0.5 s apart anyway.
- **RPO** uses the [REST API documented by MV SR](https://rpo.minv.sk/rpo-api-doc.html) at `api.statistics.sk/rpo/v1`, which needs no key and publishes no limits. Requests are spaced 0.5 s apart anyway. A detail takes about a second, while searches take 5–30 s.
- **data.slovensko.sk** (Vestník issues) publishes no limits and its robots.txt allows everything. The sync fetches one issue at a time, 2 s apart. The data are licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) by ÚVO.
- **TED** uses its [Search API](https://docs.ted.europa.eu/api/latest/index.html), which needs no key and publishes no rate limit; requests are spaced 0.5 s apart. Notice XML is cached for good, since published notices never change (a correction is a new notice). TED data may be reused free of charge with attribution (Commission Decision 2011/833/EU). ÚVO's own website disallows robots in its robots.txt and is not used.
- **Finančná správa** publishes no limits; its robots.txt is missing (404) and its servers sit behind an F5 firewall that sometimes resets a transfer. The sync spaces requests 2 s apart and retries a reset download twice. The tools send at most two HEAD requests and two small downloads every 6 hours.
- **Courts:** the ISU API publishes no limits or licence, and its robots.txt does not cover `/pilot/api`; requests are spaced 1 s apart, and page numbers are 1-based. Decision PDFs and asset declarations are fetched one at a time when a tool asks for text, and the last few are kept in memory, not on disk. Súdna rada publishes no robots.txt. No licence is published for either source.
- **RÚZ** uses its [open API](https://www.registeruz.sk/cruz-public/home/api), which needs no key and publishes no limits; robots.txt only disallows the XLS exports. Requests are spaced 0.5 s apart. Every statement and report is a separate request, so the first look at a large filer takes 10–30 s. Statements and reports are then cached for 3 days, and report templates (which RÚZ never changes) for good. RÚZ data are licensed [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
- **PPA** lists are XLSX files on www.apa.sk, which has no robots.txt and publishes no limits or licence. The sync fetches the page and the changed files only, 2 s apart.
- **ITMS21+** uses its [open-data API](https://api.itms21.sk/public/v1/openapi.json), which needs no key and publishes no limits or licence. Requests are spaced 1 s apart, one at a time. Lists are read in pages of 1000 records.

CRZ daily exports and attachments are cached in `~/.cache/sk-public-money`, or in
`$SK_PUBLIC_MONEY_CACHE_DIR` when it's set. CRZ export data is licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) by Úrad vlády SR, and RPO data under
CC BY 4.0 by MV SR.

People in these registers are often private individuals, and a matching name isn't proof that two
records are the same person.
