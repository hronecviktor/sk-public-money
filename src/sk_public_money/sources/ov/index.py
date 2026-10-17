"""The local Obchodný vestník index: kept notices (zlib-compressed JSON) and the IČOs they concern.

Built from Slovensko.Digital's weekly Datahub dump and topped up from its sync API (sync.py).
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from pathlib import Path

from ... import localdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY, date TEXT, issue TEXT, category TEXT, kind TEXT, body BLOB
);
CREATE TABLE IF NOT EXISTS companies (
    ico TEXT, notice_id INTEGER, mentioned INTEGER, PRIMARY KEY (ico, notice_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS companies_notice ON companies (notice_id);
"""
BODY_KEYS = ("title", "companies", "mentioned_icos", "text", "details")


def default_path() -> Path:
    return localdb.db_path("ov")


class OVIndex:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.db = localdb.connect(self.path, SCHEMA)

    def close(self) -> None:
        self.db.close()

    def add(self, record: dict, *, replace_links: bool = False) -> None:
        """Store one parsed notice (no commit). replace_links drops IČO links of an earlier version."""
        body = zlib.compress(json.dumps({k: record[k] for k in BODY_KEYS}, ensure_ascii=False, separators=(",", ":")).encode(), 6)
        self.db.execute(
            "INSERT OR REPLACE INTO notices (id, date, issue, category, kind, body) VALUES (?, ?, ?, ?, ?, ?)",
            (record["id"], record["date"], record["issue"], record["category"], record["kind"], body),
        )
        if replace_links:
            self.db.execute("DELETE FROM companies WHERE notice_id = ?", (record["id"],))
        links = [(c["ico"], record["id"], 0) for c in record["companies"]]
        links += [(ico, record["id"], 1) for ico in record["mentioned_icos"]]
        self.db.executemany("INSERT OR REPLACE INTO companies (ico, notice_id, mentioned) VALUES (?, ?, ?)", links)

    def drop(self, notice_id: int) -> None:
        """Forget a notice that a newer version no longer qualifies for (no commit)."""
        self.db.execute("DELETE FROM notices WHERE id = ?", (notice_id,))
        self.db.execute("DELETE FROM companies WHERE notice_id = ?", (notice_id,))

    def search(self, ico: str, *, category: str | None = None, since: str | None = None,
               limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """Notices concerning an IČO, newest first, and how many match."""
        where = ["c.ico = ?"]
        params: list = [ico]
        if category:
            where.append("n.category = ?")
            params.append(category)
        if since:
            where.append("n.date >= ?")
            params.append(since)
        sql = f"FROM companies c JOIN notices n ON n.id = c.notice_id WHERE {' AND '.join(where)}"
        total = self.db.execute(f"SELECT count(*) {sql}", params).fetchone()[0]
        rows = self.db.execute(
            f"SELECT n.id, n.date, n.issue, n.category, n.kind, n.body, c.mentioned {sql} ORDER BY n.date DESC, n.id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        out = []
        for row in rows:
            body = json.loads(zlib.decompress(row["body"]))
            out.append({"id": row["id"], "date": row["date"], "issue": row["issue"], "category": row["category"],
                        "kind": row["kind"], "mentioned_only": bool(row["mentioned"]), **body})
        return out, total

    def coverage(self) -> tuple[str | None, str | None]:
        row = self.db.execute("SELECT min(date), max(date) FROM notices").fetchone()
        return row[0], row[1]

    def get_meta(self, key: str) -> str | None:
        return localdb.get_meta(self.db, key)

    def set_meta(self, key: str, value: str) -> None:
        localdb.set_meta(self.db, key, value)

    def commit(self) -> None:
        self.db.commit()


def exists(path: Path | None = None) -> bool:
    path = path or default_path()
    if not localdb.exists(path):
        return False
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return db.execute("SELECT value FROM meta WHERE key = 'built'").fetchone() is not None
        finally:
            db.close()
    except sqlite3.Error:
        return False
