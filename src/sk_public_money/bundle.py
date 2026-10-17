"""Prebuilt index snapshots, so a first run downloads one file instead of rebuilding for hours.

Only indexes whose data may be redistributed and that are slow to build are bundled. The maintainer
builds snapshots with `sk-public-money-sync bundle --out DIR` and uploads every file of DIR to the
GitHub release tagged `indexes` (data files first, manifest.json last). A sync of a bundled source
whose index does not exist yet first downloads the snapshot (checking its hash and schema version),
then fetches only what is newer. Anything wrong with a snapshot falls back to a full build.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import lzma
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .http import SourceUnavailable, ThrottledClient
from .localdb import ensure_free_space

BUNDLE_URL = "https://github.com/hronecviktor/sk-public-money/releases/download/indexes"
XZ_MAGIC = b"\xfd7zXZ\x00"


@dataclass(frozen=True)
class Bundled:
    path: callable  # () -> Path of the index
    schema: int  # bump when an index built by older code can't be topped up by newer code
    licence: str
    drop_meta: tuple[str, ...] = ("checked_at",)  # so the tools check for new data at once
    set_meta: dict = field(default_factory=dict)


def _uvo() -> Bundled:
    from .sources.uvo import index

    return Bundled(index.default_path, index.SCHEMA_VERSION, "Vestník verejného obstarávania, ÚVO, CC BY 4.0",
                   set_meta={"keep_raw": "0"})


def _eu() -> Bundled:
    from .sources.eu import index

    # Lists reload on every run anyway; procurement details and suppliers in the snapshot are kept and
    # only new or changed ones are fetched.
    return Bundled(index.default_path, index.SCHEMA_VERSION, "licence not published, ITMS21+ open data API, MIRRI SR",
                   drop_meta=("synced_at",))


# Source name -> its bundle description. Imported lazily so the sync command stays light.
BUNDLED = {"uvo": _uvo, "eu": _eu}


def bundle_url() -> str:
    return os.environ.get("SK_PUBLIC_MONEY_BUNDLE_URL", BUNDLE_URL).rstrip("/")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(name: str, out: Path) -> dict:
    """A compact, consistent copy of a source's index, xz-compressed into out; returns its manifest entry."""
    spec = BUNDLED[name]()
    src = spec.path()
    if not src.exists():
        raise FileNotFoundError(f"no {name} index at {src}; build it with sk-public-money-sync {name}")
    out.mkdir(parents=True, exist_ok=True)
    # The working copy sits next to the index: out may be a network share where SQLite can't lock.
    with tempfile.TemporaryDirectory(dir=src.parent) as tmp:
        copy = Path(tmp) / f"{name}.sqlite"
        with sqlite3.connect(src) as live, sqlite3.connect(copy) as db:
            live.backup(db)  # consistent even while the tools are using the index
        db = sqlite3.connect(copy)
        with db:
            for key in spec.drop_meta:
                db.execute("DELETE FROM meta WHERE key = ?", (key,))
            for key, value in spec.set_meta.items():
                db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("VACUUM")
        db.close()
        target = out / f"{name}.sqlite.xz"
        with copy.open("rb") as f, lzma.open(target, "wb", preset=6) as xz:
            shutil.copyfileobj(f, xz, 1 << 20)
        size = copy.stat().st_size
    return {
        "file": target.name, "sha256": _sha256(target), "bytes": target.stat().st_size, "sqlite_bytes": size,
        "schema": spec.schema, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "licence": spec.licence,
    }


def build(out: Path, names: list[str]) -> Path:
    """Snapshot the named sources into out. Entries of other sources already in out's manifest are
    kept (when their file is still there), so refreshing one snapshot doesn't drop the others."""
    path = out / "manifest.json"
    manifest = {"format": 1, "sources": {}}
    if path.exists():
        try:
            kept = json.loads(path.read_text()).get("sources", {})
        except ValueError:
            kept = {}
        manifest["sources"] = {n: e for n, e in kept.items() if n not in names and (out / e.get("file", "")).is_file()}
    for name in names:
        print(f"{name}: snapshotting…", file=sys.stderr, flush=True)
        manifest["sources"][name] = entry = snapshot(name, out)
        print(f"{name}: {entry['file']} {entry['bytes'] / 2**20:.0f} MB (index {entry['sqlite_bytes'] / 2**20:.0f} MB)",
              file=sys.stderr, flush=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


async def _fetch(name: str, client: ThrottledClient) -> str:
    spec = BUNDLED[name]()
    resp = await client._get(f"{bundle_url()}/manifest.json")
    if resp.status_code != 200:
        raise SourceUnavailable(f"no snapshot manifest (HTTP {resp.status_code})")
    try:
        entry = resp.json()["sources"][name]
    except (ValueError, KeyError, TypeError):
        raise LookupError(f"the snapshot manifest has no {name} index") from None
    if entry.get("schema") != spec.schema:
        raise LookupError(f"the {name} snapshot was built by another version (schema {entry.get('schema')}, this "
                          f"version needs {spec.schema})")
    target = spec.path()
    ensure_free_space(target.parent, entry["bytes"] + entry.get("sqlite_bytes", 0))
    packed = target.parent / entry["file"]
    try:
        await client._download(f"{bundle_url()}/{entry['file']}", packed, expect=XZ_MAGIC)
        if _sha256(packed) != entry["sha256"]:
            raise LookupError(f"the {name} snapshot does not match its manifest (upload in progress?)")
        part = target.with_suffix(target.suffix + ".part")
        with lzma.open(packed, "rb") as xz, part.open("wb") as f:
            shutil.copyfileobj(xz, f, 1 << 20)
        part.replace(target)
    finally:
        packed.unlink(missing_ok=True)
        target.with_suffix(target.suffix + ".part").unlink(missing_ok=True)
    return entry["built_at"]


def bootstrap(name: str, client: ThrottledClient | None = None) -> bool:
    """Download the snapshot of a bundled source whose index does not exist yet. Returns whether it
    did; every failure is reported and leaves the normal full build to run."""
    if name not in BUNDLED or BUNDLED[name]().path().exists():
        return False
    print(f"{name}: downloading the prebuilt index…", file=sys.stderr, flush=True)

    async def run() -> str:
        c = client or ThrottledClient(lambda: 1.0)
        try:
            return await _fetch(name, c)
        finally:
            if client is None:
                await c.aclose()

    try:
        built = asyncio.run(run())
    except (SourceUnavailable, LookupError, OSError, lzma.LZMAError) as exc:
        print(f"{name}: no prebuilt index ({exc}); building it from the source instead.", file=sys.stderr, flush=True)
        return False
    print(f"{name}: prebuilt index from {built}; fetching what is newer.", file=sys.stderr, flush=True)
    return True


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="sk-public-money-sync bundle",
        description="Maintainers: write snapshots of the bundled indexes and their manifest to a folder, to "
                    "upload to the GitHub release tagged 'indexes' (data files first, manifest.json last).",
    )
    parser.add_argument("--out", type=Path, required=True, help="folder for the snapshot files")
    parser.add_argument("sources", nargs="*", default=list(BUNDLED), help=f"default: {' '.join(BUNDLED)}")
    args = parser.parse_args(argv)
    unknown = set(args.sources) - BUNDLED.keys()
    if unknown:
        parser.error(f"not bundled: {', '.join(sorted(unknown))}; bundled: {', '.join(BUNDLED)}")
    path = build(args.out, args.sources)
    print(f"wrote {path}; upload with:\n  gh release upload indexes {' '.join(str(args.out / f'{n}.sqlite.xz') for n in args.sources)} "
          f"--clobber\n  gh release upload indexes {path} --clobber", file=sys.stderr)
