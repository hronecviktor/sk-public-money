"""Entry point: one MCP server with tools for every source."""

from __future__ import annotations

import logging

from .app import mcp
from .sources.crz import tools as _crz_tools  # noqa: F401  (registers crz_* tools)
from .sources.court import tools as _court_tools  # noqa: F401  (registers court_* tools)
from .sources.fs import tools as _fs_tools  # noqa: F401  (registers fs_* tools)
from .sources.insolvency import tools as _insolvency_tools  # noqa: F401  (registers insolvency_* tools)
from .sources.ov import tools as _ov_tools  # noqa: F401  (registers ov_* tools)
from .sources.party import tools as _party_tools  # noqa: F401  (registers party_* tools)
from .sources.ppa import tools as _ppa_tools  # noqa: F401  (registers ppa_* tools)
from .sources.rpo import tools as _rpo_tools  # noqa: F401  (registers rpo_* tools)
from .sources.rpvs import tools as _rpvs_tools  # noqa: F401  (registers rpvs_* tools)
from .sources.ruz import tools as _ruz_tools  # noqa: F401  (registers ruz_* tools)
from .sources.uvo import tools as _uvo_tools  # noqa: F401  (registers uvo_* tools)


def main() -> None:
    # httpx logs every request at INFO, and pypdf warns about each malformed PDF it still reads.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    mcp.run()


if __name__ == "__main__":
    main()
