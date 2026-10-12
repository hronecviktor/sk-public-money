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

RÚZ, Register účtovných závierok (ruz_* tools): financial statements of companies, sole traders,
non-profits and public bodies. ruz_get_financials reads revenue, profit, assets and equity per year and
names the report row and column of each figure; anything it could not map is listed as not_mapped, so
do not fill gaps by guessing. Companies reporting under IFRS file only PDFs (linked). Data are CC0.

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
