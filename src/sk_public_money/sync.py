"""sk-public-money-sync <source> [options]: build or top up a source's local index.

Each source with a local index has a sync.py whose main(argv) takes the remaining arguments.
`sk-public-money-sync all` runs every source in turn with its default options. A source with a
prebuilt snapshot (bundle.py) whose index does not exist yet starts from the snapshot, unless
--no-bootstrap is given.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time

from . import bundle

# Source name -> module with main(argv). Add a line here for each source with an index, in the order
# `all` builds them: quick ones first, so a long build that is stopped leaves the rest done.
SOURCES = {
    "party": "sk_public_money.sources.party.sync",
    "ppa": "sk_public_money.sources.ppa.sync",
    "fs": "sk_public_money.sources.fs.sync",
    "court": "sk_public_money.sources.court.sync",
    "uvo": "sk_public_money.sources.uvo.sync",
    "eu": "sk_public_money.sources.eu.sync",
}


def run_all(argv: list[str], *, bootstrap: bool = True) -> None:
    parser = argparse.ArgumentParser(
        prog="sk-public-money-sync all",
        description="Build or top up every local index in turn, quick ones first. Each source fetches only "
                    "what it is missing, so this is also the command to run regularly (e.g. daily). The slow "
                    f"ones ({', '.join(bundle.BUNDLED)}) start from a prebuilt snapshot on their first run.",
    )
    parser.add_argument("--skip", default="", help="comma-separated sources to leave out, e.g. --skip uvo")
    parser.add_argument("--no-bootstrap", action="store_true", help="build every index from the sources, without snapshots")
    args = parser.parse_args(argv)
    bootstrap = bootstrap and not args.no_bootstrap
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    unknown = skip - SOURCES.keys()
    if unknown:
        parser.error(f"unknown source(s): {', '.join(sorted(unknown))}; sources: {', '.join(SOURCES)}")
    results: list[tuple[str, str]] = []
    for name, module in SOURCES.items():
        if name in skip:
            continue
        print(f"\n=== {name} ===", file=sys.stderr, flush=True)
        started = time.monotonic()
        try:
            if bootstrap:
                bundle.bootstrap(name)
            importlib.import_module(module).main([])
            outcome = "ok"
        except SystemExit as exc:
            outcome = "ok" if exc.code in (None, 0) else f"failed (exit {exc.code})"
        except KeyboardInterrupt:
            results.append((name, "stopped"))
            break
        except Exception as exc:  # noqa: BLE001  (one source failing must not stop the others)
            outcome = f"failed: {type(exc).__name__}: {exc}"
        results.append((name, f"{outcome}, {(time.monotonic() - started) / 60:.1f} min"))
    print("\n=== summary ===", file=sys.stderr)
    for name, outcome in results:
        print(f"  {name}: {outcome}", file=sys.stderr)
    if any(not outcome.startswith("ok") for _, outcome in results):
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "all":
        return run_all(argv[1:])
    if argv and argv[0] == "bundle":
        return bundle.main(argv[1:])
    if not argv or argv[0] not in SOURCES:
        names = ", ".join(SOURCES)
        sys.exit(f"usage: sk-public-money-sync <source>|all [options]  (sources: {names}; <source> --help for its options)")
    rest = [a for a in argv[1:] if a != "--no-bootstrap"]
    if "--no-bootstrap" not in argv and not {"-h", "--help"} & set(rest):
        bundle.bootstrap(argv[0])
    importlib.import_module(SOURCES[argv[0]]).main(rest)


if __name__ == "__main__":
    main()
