"""The MCP server instance and helpers shared by every source's tools."""

from __future__ import annotations

import functools

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .http import SourceUnavailable

INSTRUCTIONS = """\
Tools for Slovak public-money registers. Records are in Slovak. Companies are identified by IČO
(8 digits), which links the sources: take an IČO from one tool and look it up in another.

CRZ, Centrálny register zmlúv (crz_* tools): contracts that state bodies must publish. Buyer =
objednávateľ (the public body), supplier = dodávateľ. The portal rate-limits robots (one request per
2 s by day), so calls can take a few seconds; prefer narrow searches. Daily export data is CC BY 4.0
by Úrad vlády SR.

RPVS, Register partnerov verejného sektora (rpvs_* tools): companies and people that receive public
money must register their beneficial owners (konečný užívateľ výhod, KUV). When the partner or its
owner is a listed company or no person owns 25 %, RPVS lists top management as the beneficial owners,
so they are not necessarily shareholders. Person and name searches ignore case and diacritics
(Siroky finds Široký).

RPO, Register právnických osôb (rpo_* tools): the official identity of every Slovak company, sole
trader, public body and association by IČO: names over time, seat, legal form, founding and
dissolution, statutory body (konateľ, predstavenstvo), stakeholders (spoločník, akcionár, dozorná
rada, prokurista), capital, predecessors and successors. RPO has no birth dates and no beneficial
owners. Data lags the source registers by up to a day. Searches ignore case and diacritics; the API
is slow (searches 5-30 s). Current directors and procurists (rpo_get_organization) and the roles
found by rpo_search_person carry a 'disqualified' flag when their name is in the register of
disqualified persons: matched by name only (strengthened when the address or town matches, as said
in matched_by), with the ban period and whether it is active; court_disqualified shows the record.
CC BY 4.0 by MV SR.

RÚZ, Register účtovných závierok (ruz_* tools): financial statements of companies, sole traders,
non-profits and public bodies. ruz_get_financials reads revenue, profit, assets and equity per year and
names the report row and column of each figure; anything it could not map is listed as not_mapped, so
do not fill gaps by guessing. Companies reporting under IFRS file only PDFs (linked). Data are CC0.

Public procurement, verejné obstarávanie (uvo_* tools): who bought what by which procedure, how many
tenders came in, who won and for how much. Sources: the Vestník verejného obstarávania (ÚVO's journal,
every notice since October 2023 also below the EU thresholds, and buyers' half-year reports of
low-value contracts with suppliers, values without VAT and CRZ links), kept in a local index; and TED
(procurements above the EU thresholds, back to 2016). Each result names its source. Contracts carry
CRZ IDs for crz_get_contract. Values of framework agreements (rámcová dohoda) are maximums, not
spending; a value of 0 means undisclosed. Identifiers are labelled ico, dic (DIČ), ic_dph or foreign;
ico is null when the notice gives no Slovak IČO. Vestník data CC BY 4.0 by ÚVO; TED data may be reused
with attribution.

Finančná správa SR, the tax authority (fs_* tools): its published lists, kept in a local index (built
with sk-public-money-sync fs): VAT registration, index daňovej spoľahlivosti (tax reliability rating),
VAT payers with reasons to cancel their registration (§ 52(6)) and struck off (§ 52(8)), corporate
income tax assessed and tax loss per tax period, VAT excess deduction or own liability per period
(both from every quarterly list since the oldest archived, June 2018; a period published in several lists shows each with
list_of), and tax debtors. The debtor list has no IČO, so
debtors are matched by name only (matched_by: name). Natural persons appear as published. Amounts in
EUR. CC BY 4.0 (the reliability index CC0): Zdroj údajov: Finančná správa SR - https://opendata.financnasprava.sk/.

Courts, súdy (court_* tools): decisions of Slovak general courts (~4.7M since about 2000) and judges,
from the Ministry of Justice's pilot API (obcan.justice.sk), and judges' yearly asset declarations
from Súdna rada. People who are parties are anonymised in decisions; companies keep name and IČO.
court_search_decisions by IČO is a full-text match (both 31322832 and 31 322 832 spellings): check
the snippet, the number may belong to another party. Decision text comes only from the PDF
(court_get_decision text=true). Asset declarations are matched to judges by name.
court_disqualified searches the register of disqualified persons (people banned by a court or tax
office from company statutory bodies; ~1000 records, kept in a local copy refreshed daily) by name,
with address, case and ban period; it has no birth dates. No licence is published for these sources.

PPA, Pôdohospodárska platobná agentúra (ppa_* tools): farm and rural-development subsidies of the
Common Agricultural Policy per beneficiary and EU financial year (FY2025 = 16 Oct 2024 to 15 Oct
2025; the last two years), split into EPZF (EAGF), EPFRV (EAFRD) and national co-financing, by
measure code (Annex IX of Reg. 2022/128) and obec, from a local index. The lists carry no IČO: a
company is found by name, and results say matched_by name. Recipients of EUR 1,250 or less are only a
numeric code. No licence is stated; published under Article 98 of Reg. 2021/2116.

Political-party financing (party_* tools). party_account_transactions: transparent accounts
(transparentný účet) at Slovenská sporiteľňa, every movement with date, signed amount (negative = paid
out), counterparty name as the bank shows it, note and variable symbol; no counterparty IBAN or IČO.
The account list mixes parties, candidates, campaigns and charities; other banks (ČSOB, VÚB, Tatra
banka, Fio) are not covered; no terms of use published. Donors, lenders and membership fees are in the
parties' annual reports (výročné správy, 2015 on, minv.sk, no licence published): find the report with
party_annual_reports, then party_read_report. Most reports are scans: read_report returns text pages
as text and scanned pages as images for you to read; without pages it gives coverage and thumbnails to
find the registers (usually G gifts, F loans, J membership fees, K overdue liabilities). A report with
no rows found is not proof of no donors. Check every name and amount read from an image against it.

EU funds 2021-2027, ITMS21+ (eu_* tools): projects co-financed by the EU (Program Slovensko, fisheries,
Interreg SK-AT and SK-CZ, home-affairs funds AMIF, ISF, BMVI), grant applications (ŽoNFP), payment
claims (ŽoP, amounts the recipient claimed, not necessarily paid), the procurements recipients ran for
their projects with contracts and main suppliers, and invoices billed to projects. Amount paid to the
recipient is in eu_get_project only. Searches read a local index (sk-public-money-sync eu); recently
changed records may be missing. 2014-2020 projects (ITMS2014+) and the Recovery Plan are not covered.
No licence is published for the data.

People named in these registers are often private individuals; a matching name is not proof of the
same person.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

mcp = MCPServer("sk-public-money", instructions=INSTRUCTIONS)


def user_errors(fn):
    """Report anticipated failures to the model with their message instead of a bare error."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (ValueError, LookupError, FileNotFoundError, SourceUnavailable) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper
