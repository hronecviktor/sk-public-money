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
is slow (searches 5-30 s). CC BY 4.0 by MV SR.

RÚZ, Register účtovných závierok (ruz_* tools): financial statements of companies, sole traders,
non-profits and public bodies. ruz_get_financials reads revenue, profit, assets and equity per year and
names the report row and column of each figure; anything it could not map is listed as not_mapped, so
do not fill gaps by guessing. Companies reporting under IFRS file only PDFs (linked). Data are CC0.

Public procurement, verejné obstarávanie (uvo_* tools): who bought what by which procedure, how many
tenders came in, who won and for how much. Notices come from TED, where Slovak buyers publish
procurements above the EU thresholds (from 2016). A notice's contracts carry CRZ IDs for
crz_get_contract. Values of framework agreements (rámcová dohoda) are maximums, not spending; a value
of 0 means undisclosed. Identifiers are labelled ico, dic (DIČ), ic_dph or foreign; ico is null when
the notice gives no Slovak IČO. TED data may be reused with attribution.

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
