"""sk-public-money-sync <source> [options]: build or top up a source's local index.

Each source with a local index has a sync.py whose main(argv) takes the remaining arguments.
`sk-public-money-sync all` runs every source with its default options, several at once: each
source talks to its own portals, so running them side by side never sends one portal more than its
own client allows. A source with a prebuilt snapshot (bundle.py) whose index does not exist yet
starts from the snapshot, unless --no-bootstrap is given.
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import bundle, eta
from .http import cache_dir

# Source name -> module with main(argv). Add a line here for each source with an index, ordered from
# the quickest first build to the slowest: `all --jobs 1` runs them in this order (so a long build
# that is stopped leaves the rest done), and in parallel the slowest start first.
SOURCES = {
    "party": "sk_public_money.sources.party.sync",
    "ppa": "sk_public_money.sources.ppa.sync",
    "fs": "sk_public_money.sources.fs.sync",
    "court": "sk_public_money.sources.court.sync",
    "uvo": "sk_public_money.sources.uvo.sync",
    "ov": "sk_public_money.sources.ov.sync",
    "eu": "sk_public_money.sources.eu.sync",
}
JOBS = 4


def run_all(argv: list[str], *, bootstrap: bool = True) -> None:
    parser = argparse.ArgumentParser(
        prog="sk-public-money-sync all",
        description="Build or top up every local index, several sources at once. Each source fetches only "
                    "what it is missing, so this is also the command to run regularly (e.g. daily). The slow "
                    f"ones ({', '.join(bundle.BUNDLED)}) start from a prebuilt snapshot on their first run.",
    )
    parser.add_argument("--skip", default="", help="comma-separated sources to leave out, e.g. --skip ov")
    parser.add_argument("--jobs", type=int, default=JOBS,
                        help=f"sources synced at the same time (default {JOBS}); 1 runs them one after another, quickest first")
    parser.add_argument("--no-bootstrap", action="store_true", help="build every index from the sources, without snapshots")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    bootstrap = bootstrap and not args.no_bootstrap
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    unknown = skip - SOURCES.keys()
    if unknown:
        parser.error(f"unknown source(s): {', '.join(sorted(unknown))}; sources: {', '.join(SOURCES)}")
    names = [name for name in SOURCES if name not in skip]
    history = eta.load_history()
    kinds = {name: eta.kind(name) for name in names}
    expected = {name: eta.expected(name, kinds[name], bootstrap, history) for name in names}
    if args.jobs == 1:
        results = _run_in_turn(names, bootstrap, kinds, expected)
    else:
        results = _run_parallel(names, args.jobs, bootstrap, kinds, expected)
    print("\n=== summary ===", file=sys.stderr)
    for name in names:
        if name in results:
            print(f"  {name}: {results[name]}", file=sys.stderr)
    if any(not outcome.startswith("ok") for outcome in results.values()) or len(results) < len(names):
        sys.exit(1)


def _run_in_turn(names: list[str], bootstrap: bool, kinds: dict[str, str] | None = None,
                 expected: dict[str, float] | None = None) -> dict[str, str]:
    """Each source in this process, one after another, with its output as it comes."""
    results: dict[str, str] = {}
    kinds = kinds or {}
    expected = expected or {}
    for i, name in enumerate(names):
        left = sum(expected.get(n, 0) for n in names[i:])
        estimate = f" (expected {eta.human(expected[name])}; all done in {eta.human(left)}, around {_clock(left)})" if name in expected else ""
        print(f"\n=== {name} ==={estimate}", file=sys.stderr, flush=True)
        started = time.monotonic()
        try:
            if bootstrap:
                bundle.bootstrap(name)
            importlib.import_module(SOURCES[name]).main([])
            outcome = "ok"
        except SystemExit as exc:
            outcome = "ok" if exc.code in (None, 0) else f"failed (exit {exc.code})"
        except KeyboardInterrupt:
            results[name] = "stopped"
            break
        except Exception as exc:  # noqa: BLE001  (one source failing must not stop the others)
            outcome = f"failed: {type(exc).__name__}: {exc}"
        took = time.monotonic() - started
        if outcome == "ok" and name in kinds:
            eta.remember(name, kinds[name], took)
        results[name] = f"{outcome}, {_minutes(took)}"
    return results


def log_path(name: str) -> Path:
    return cache_dir() / "logs" / f"{name}.log"


def child_command(name: str, bootstrap: bool) -> list[str]:
    """The command that syncs one source in its own process."""
    return [sys.executable, "-m", "sk_public_money.sync", name] + ([] if bootstrap else ["--no-bootstrap"])


def _run_parallel(names: list[str], jobs: int, bootstrap: bool, kinds: dict[str, str] | None = None,
                  expected: dict[str, float] | None = None) -> dict[str, str]:
    """Each source in its own process, at most `jobs` at once, slowest first. Their output goes to a log
    file per source; the terminal shows one status line per source and the expected time left."""
    waiting = list(reversed(names))  # SOURCES runs from quickest to slowest
    running: dict[str, tuple[subprocess.Popen, float]] = {}
    results: dict[str, str] = {}
    kinds = kinds or {}
    expected = expected or {name: 60.0 for name in names}
    status = _Status(names, sys.stderr, expected, jobs)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        while waiting or running:
            while waiting and len(running) < jobs:
                name = waiting.pop(0)
                path = log_path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as log:
                    proc = subprocess.Popen(child_command(name, bootstrap), stdout=log, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, env=env)
                running[name] = (proc, time.monotonic())
                status.event(f"{name}: started (log: {path})", running, waiting)
            for name, (proc, started) in list(running.items()):
                code = proc.poll()
                if code is None:
                    continue
                del running[name]
                took = time.monotonic() - started
                outcome = "ok" if code == 0 else f"failed (exit {code}; see {log_path(name)})"
                if code == 0 and name in kinds:
                    eta.remember(name, kinds[name], took)
                results[name] = f"{outcome}, {_minutes(took)}"
                status.event(f"{name}: {results[name]}", running, waiting)
            status.draw(running, results, waiting)
            time.sleep(0.5)
    except KeyboardInterrupt:
        for name, (proc, _) in running.items():
            _stop(proc)
            results[name] = "stopped"
        for name in waiting:
            results[name] = "not started"
    status.draw(running={}, results=results, waiting=[], final=True)
    return results


def _stop(proc: subprocess.Popen) -> None:
    """Let a sync stop the way Ctrl+C stops it (it keeps what it indexed), then insist. A Ctrl+C typed
    in the terminal already reached the children, so give them a moment before interrupting again."""
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name != "nt":
            proc.send_signal(signal.SIGINT)
        proc.wait(timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        proc.kill()


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f} min"


def _clock(seconds_from_now: float) -> str:
    return time.strftime("%H:%M", time.localtime(time.time() + seconds_from_now))


def last_line(path: Path, width: int) -> str:
    """The last non-empty line a sync wrote, for its status line."""
    try:
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - 4096))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    lines = [line.strip() for line in tail.replace("\r", "\n").split("\n") if line.strip()]
    text = lines[-1] if lines else ""
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


class _Status:
    """On a terminal: a block of one line per source, redrawn in place. Elsewhere (cron, a pipe):
    only starts and finishes, one line each."""

    def __init__(self, names: list[str], out, expected: dict[str, float] | None = None, jobs: int = 1):
        self.names, self.out = names, out
        self.expected = expected or {}
        self.jobs = jobs
        self.live = out.isatty() and os.environ.get("TERM") != "dumb"
        self.drawn = 0
        self.next_draw = 0.0

    def _left(self, name: str, started: float) -> float:
        return eta.remaining(time.monotonic() - started, self.expected.get(name, 60.0), last_line(log_path(name), 400))

    def left(self, running: dict, waiting: list) -> float:
        """Seconds until every source is done."""
        return eta.total([self._left(n, r[1]) for n, r in running.items()],
                         [self.expected.get(n, 60.0) for n in waiting], self.jobs)

    def event(self, text: str, running: dict | None = None, waiting: list | None = None) -> None:
        if self.live:
            return
        if running or waiting:
            left = self.left(running or {}, waiting or [])
            text += f"; all done in {eta.human(left)}, around {_clock(left)}"
        print(f"{time.strftime('%H:%M:%S')} {text}", file=self.out, flush=True)

    def draw(self, running: dict, results: dict, waiting: list, final: bool = False) -> None:
        if not self.live or (not final and time.monotonic() < self.next_draw):
            return
        self.next_draw = time.monotonic() + 1.0
        width = shutil.get_terminal_size((100, 20)).columns
        lines = []
        if running or waiting:
            left = self.left(running, waiting)
            lines.append(f"all done in {eta.human(left)}, around {_clock(left)}")
        for name in self.names:
            if name in running:
                elapsed = time.monotonic() - running[name][1]
                line = last_line(log_path(name), 400)
                left = ("longer than expected" if eta.overdue(elapsed, self.expected.get(name, 60.0), line)
                        else eta.human(self._left(name, running[name][1])) + " left")
                head = f"{name:<6} {int(elapsed // 60):>3}:{int(elapsed % 60):02d}  {left:<21}"
                lines.append(head + last_line(log_path(name), width - len(head) - 1))
            elif name in results:
                lines.append(f"{name:<6} {results[name]}"[: width - 1])
            elif name in waiting:
                lines.append(f"{name:<6} waiting")
        if self.drawn:
            self.out.write(f"\x1b[{self.drawn}F\x1b[J")
        self.out.write("\n".join(lines) + "\n")
        self.out.flush()
        self.drawn = len(lines)


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
