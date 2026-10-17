"""Helpers for sources that keep a local SQLite index: where it lives, its meta table, when it is
due for a refresh, and a check for free disk space before a large download.

A source with an index has a sync.py with main(argv) that builds or tops it up, run from the
command line as `sk-public-money-sync <source>`; its tools only open the database and, when it is
stale, fetch a little inline (see sources/uvo for the pattern).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path

from .http import cache_dir

META = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
REPO = "git+https://github.com/hronecviktor/sk-public-money"


def sync_command(source: str) -> str:
    """How to run a source's sync, for tool messages. The server usually runs through uvx, which puts
    no sk-public-money-sync on the user's PATH, so the uvx form comes first."""
    return f"`uvx --from {REPO} sk-public-money-sync {source}` (or `sk-public-money-sync {source}` where the package is installed)"


def db_path(source: str, name: str | None = None) -> Path:
    """The index file of a source: <cache>/<source>/<name or source>.sqlite."""
    return cache_dir() / source / f"{name or source}.sqlite"


def connect(path: Path, schema: str = "") -> sqlite3.Connection:
    """Open (creating if needed) an index with a meta table and the given schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(META + schema)
    return db


def exists(path: Path) -> bool:
    """Whether an index has been built (the tools say how to build it when not)."""
    return path.exists() and path.stat().st_size > 0


def get_meta(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    with db:
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def is_due(db: sqlite3.Connection, key: str, every: float) -> bool:
    """Whether more than `every` seconds passed since mark_done(db, key)."""
    last = get_meta(db, key)
    return not last or time.time() - float(last) >= every


def mark_done(db: sqlite3.Connection, key: str) -> None:
    set_meta(db, key, str(time.time()))


def _alive(pid: int, claim: Path) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) would send Ctrl+C there; trust recent claims instead
        return time.time() - claim.stat().st_mtime < 12 * 3600
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _claimed_by_others() -> int:
    """Bytes other running processes said they are about to write (see ensure_free_space)."""
    total = 0
    for claim in (cache_dir() / ".claims").glob("*"):
        try:
            pid = int(claim.name)
            if pid == os.getpid():
                continue
            if not _alive(pid, claim):
                claim.unlink(missing_ok=True)
                continue
            total += int(claim.read_text() or 0)
        except (ValueError, OSError):
            continue
    return total


def ensure_free_space(path: Path, needed: int, *, reserve: int = 2 << 30) -> None:
    """Refuse to start a download of `needed` bytes that would leave less than `reserve` free.

    Syncs run in parallel (sk-public-money-sync all), so each process records its latest request
    in <cache>/.claims/<pid> and the others count it as taken until that process ends."""
    probe = path if path.exists() else next((p for p in path.parents if p.exists()), Path("/"))
    free = shutil.disk_usage(probe).free
    others = _claimed_by_others()
    if free - others - needed < reserve:
        taken = f" ({others / 2**30:.1f} GB of it claimed by other running syncs)" if others else ""
        raise OSError(
            f"Not enough disk space in {probe}: {free / 2**30:.1f} GB free{taken}, this needs about "
            f"{needed / 2**30:.1f} GB and leaves {reserve / 2**30:.0f} GB spare."
        )
    claims = cache_dir() / ".claims"
    try:
        claims.mkdir(parents=True, exist_ok=True)
        (claims / str(os.getpid())).write_text(str(needed))
    except OSError:
        pass  # a claim is a courtesy to parallel syncs, never a reason to fail
