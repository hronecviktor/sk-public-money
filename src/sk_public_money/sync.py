"""sk-public-money-sync <source> [options]: build or top up a source's local index.

Each source with a local index has a sync.py whose main(argv) takes the remaining arguments.
"""

from __future__ import annotations

import importlib
import sys

# Source name -> module with main(argv). Add a line here for each source with an index.
SOURCES = {
    "fs": "sk_public_money.sources.fs.sync",
    "uvo": "sk_public_money.sources.uvo.sync",
    "ppa": "sk_public_money.sources.ppa.sync",
    "eu": "sk_public_money.sources.eu.sync",
}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in SOURCES:
        names = ", ".join(sorted(SOURCES))
        sys.exit(f"usage: sk-public-money-sync <source> [options]  (sources: {names}; <source> --help for its options)")
    importlib.import_module(SOURCES[argv[0]]).main(argv[1:])


if __name__ == "__main__":
    main()
