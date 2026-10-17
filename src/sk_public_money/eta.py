"""How long `sk-public-money-sync all` will still take.

Each source's duration is expected from, in order: how long it took the last time on this machine
(first builds and updates are remembered apart), a built-in first-build time when the source has
never been synced here, or a short update. While a source runs, a progress figure in its log
("45 %", "[3/34]", "120 of 900") refines the estimate. The run's total is simulated over the job
slots, so sources still waiting count too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .http import cache_dir

# Minutes of a first build from the portals, measured on a 4-core VM; with a snapshot for the bundled ones.
FIRST_BUILD = {"party": 0.5, "ppa": 1, "fs": 15, "court": 20, "uvo": 45, "ov": 70, "eu": 300}
FROM_SNAPSHOT = {"uvo": 1.5, "eu": 6}
# Minutes of an update when nothing is remembered yet; eu reloads all its lists every time.
UPDATE = {"eu": 5.5}
DEFAULT_UPDATE = 1.0
DEFAULT_FIRST = 5.0
PROGRESS = (
    re.compile(r"\[(\d+)\s*/\s*(\d+)\]"),
    re.compile(r"\b(\d+) of (\d+)\b"),
    re.compile(r"(\d+(?:\.\d+)?)\s*%"),
)


def history_path() -> Path:
    return cache_dir() / "logs" / "durations.json"


def load_history() -> dict[str, float]:
    try:
        return {k: float(v) for k, v in json.loads(history_path().read_text()).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def remember(name: str, kind: str, seconds: float) -> None:
    """Record a successful run's duration for next time's estimate."""
    history = load_history()
    history[f"{name}:{kind}"] = round(seconds, 1)
    try:
        history_path().parent.mkdir(parents=True, exist_ok=True)
        history_path().write_text(json.dumps(history, indent=1, sort_keys=True))
    except OSError:
        pass


def kind(name: str) -> str:
    """'update' when the source has synced data here before, else 'first'."""
    folder = cache_dir() / name
    return "update" if folder.is_dir() and any(folder.iterdir()) else "first"


def expected(name: str, run_kind: str, bootstrap: bool, history: dict[str, float]) -> float:
    """Expected seconds for one source's run."""
    remembered = history.get(f"{name}:{run_kind}")
    if remembered is not None:
        return remembered
    if run_kind == "update":
        return UPDATE.get(name, DEFAULT_UPDATE) * 60
    if bootstrap and name in FROM_SNAPSHOT:
        return FROM_SNAPSHOT[name] * 60
    return FIRST_BUILD.get(name, DEFAULT_FIRST) * 60


def progress(line: str) -> float | None:
    """The share done that a log line reports, if any."""
    for pattern in PROGRESS:
        m = pattern.search(line)
        if not m:
            continue
        if m.lastindex == 2:
            done, total = int(m.group(1)), int(m.group(2))
            return done / total if total else None
        value = float(m.group(1))
        return value / 100 if 0 <= value <= 100 else None
    return None


def remaining(elapsed: float, expected_total: float, log_line: str = "") -> float:
    """Seconds a running source still needs: its expected time, blended with the progress it logs
    (trusted more as it advances, since early progress is often uneven)."""
    by_time = max(expected_total - elapsed, 0.1 * expected_total, 5.0)
    share = progress(log_line)
    if share is None or not 0.02 < share < 1 or elapsed < 20:
        return by_time
    by_progress = elapsed * (1 - share) / share
    return share * by_progress + (1 - share) * by_time


def overdue(elapsed: float, expected_total: float, log_line: str = "") -> bool:
    """Running clearly longer than expected, with no progress figure to go by."""
    return elapsed > 1.2 * expected_total + 30 and progress(log_line) is None


def total(running: list[float], waiting: list[float], jobs: int) -> float:
    """Seconds until everything is done: waiting sources take the first free slot, in order."""
    slots = sorted(running) + [0.0] * max(jobs - len(running), 0)
    for seconds in waiting:
        slots.sort()
        slots[0] += seconds
    return max(slots, default=0.0)


def human(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "<1 min"
    if minutes < 60:
        return f"~{minutes} min"
    return f"~{minutes // 60} h {minutes % 60:02d} min"
