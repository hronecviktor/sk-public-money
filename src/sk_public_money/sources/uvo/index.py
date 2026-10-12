"""A local SQLite index of Vestník notices, for searching by IČO, words, CPV and date.

The Vestník has no search API, only one JSON file per issue, so issues are downloaded once
(sync.py) and their notices stored here: a compact summary row and the full notice (both zlib-
compressed JSON), the organisations by role and IČO, the CPV codes, and a full-text index of the
folded text (no case, no diacritics). The database lives in the cache directory.
"""

from __future__ import annotations

import json
import re
import sqlite3
import zlib
from pathlib import Path

from ...http import cache_dir
from ...text import fold

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    year INTEGER, number INTEGER, published TEXT, dataset TEXT, modified TEXT, notices INTEGER,
    indexed_at TEXT, PRIMARY KEY (year, number)
);
CREATE TABLE IF NOT EXISTS notices (
    id TEXT PRIMARY KEY, year INTEGER, number INTEGER, published TEXT, kind TEXT, form_type TEXT,
    notice_uuid TEXT, award INTEGER, summary BLOB, detail BLOB
);
CREATE INDEX IF NOT EXISTS notices_published ON notices (published);
CREATE INDEX IF NOT EXISTS notices_issue ON notices (year, number);
CREATE INDEX IF NOT EXISTS notices_uuid ON notices (notice_uuid);
CREATE TABLE IF NOT EXISTS parties (notice_id TEXT, role TEXT, ico TEXT);
CREATE INDEX IF NOT EXISTS parties_ico ON parties (ico, role);
CREATE INDEX IF NOT EXISTS parties_notice ON parties (notice_id);
CREATE TABLE IF NOT EXISTS cpv (notice_id TEXT, code TEXT);
CREATE INDEX IF NOT EXISTS cpv_code ON cpv (code);
CREATE INDEX IF NOT EXISTS cpv_notice ON cpv (notice_id);
CREATE VIRTUAL TABLE IF NOT EXISTS search_text USING fts5 (id UNINDEXED, text, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
# Search roles -> stored roles. Winners are tenderers too; "any" is buyer or winner.
ROLES = {
    "any": "('buyer','winner')",
    "buyer": "('buyer')",
    "winner": "('winner')",
    "tenderer": "('winner','tenderer')",
    "subcontractor": "('subcontractor')",
}
# Forms that report who won: results and the half-year summary reports.
AWARD_FORMS = ("result",)


def default_path() -> Path:
    return cache_dir() / "uvo" / "vestnik.sqlite"


def _pack(value: dict) -> bytes:
    return zlib.compress(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), 6)


def _unpack(blob: bytes) -> dict:
    return json.loads(zlib.decompress(blob))


def _compact(p: dict) -> dict:
    """An organisation as listed in search results: no address or contacts."""
    return {k: v for k, v in p.items() if k in ("name", "ico", "other_ids", "ico_note", "country") and not (k == "country" and v == "SVK")}


def roles(notice: dict) -> list[tuple[str, dict]]:
    """Every organisation of a notice with its role: buyer, winner, tenderer (lost) or subcontractor."""
    out = [("buyer", b) for b in notice.get("buyers", [])]
    for lot in notice.get("lots", []):
        for result in lot.get("results", []):
            for t in result.get("tenders", []):
                out += [("winner" if t.get("winner") else "tenderer", p) for p in t.get("tenderers", [])]
                out += [("subcontractor", p) for p in t.get("subcontractors", [])]
            for c in result.get("contracts", []):
                out += [("winner", p) for p in c.get("suppliers", [])]
    return out


def summary(notice: dict) -> dict:
    """The search-result row of a notice."""
    winners, seen = [], set()
    for role, p in roles(notice):
        key = p.get("ico") or p.get("name")
        if role == "winner" and key not in seen:
            seen.add(key)
            winners.append(_compact(p))
    results = [r for lot in notice.get("lots", []) for r in lot.get("results", [])]
    contracts = [c for r in results for c in r.get("contracts", [])] + notice.get("other_contracts", [])
    reported = [c["value_excl_vat"] for c in contracts if c.get("value_excl_vat")]
    row = {
        "id": notice["id"],
        "source": notice["source"],
        "published": notice.get("published"),
        "issue": notice.get("issue"),
        "kind": notice.get("kind"),
        "notice_type": notice.get("notice_type"),
        "procedure_type": notice.get("procedure_type"),
        "title": notice.get("title"),
        "period": notice.get("period"),
        "buyers": [_compact(b) for b in notice.get("buyers", [])],
        "winners": winners,
        "cpv": notice.get("cpv"),
        "estimated_value": notice.get("estimated_value"),
        "total_value": notice.get("total_value"),
        "value_excl_vat": {"amount": round(sum(v["amount"] for v in reported), 2), "currency": reported[0]["currency"]} if reported else None,
        "framework_maximum": notice.get("framework_maximum"),
        "tenders_received": [r["bids"]["tenders"] for r in results if "tenders" in r.get("bids", {})],
        "contract_numbers": list(dict.fromkeys(c["number"] for c in contracts if c.get("number"))),
        "crz_ids": list(dict.fromkeys(c["crz_id"] for c in contracts if c.get("crz_id"))),
        "notice_uuid": notice.get("notice_uuid"),
        "uvo_procurement_id": notice.get("uvo_procurement_id"),
        "notes": notice.get("notes"),
    }
    return {k: v for k, v in row.items() if v not in (None, [], "", {})}


def _search_text(notice: dict) -> str:
    parts = [notice.get(k) or "" for k in ("title", "description", "internal_id", "kind")]
    parts += [lot.get("title") or "" for lot in notice.get("lots", [])]
    parts += [p.get("name") or "" for _, p in roles(notice)]
    return fold(" ".join(parts))


def fts_query(text: str) -> str | None:
    """Words that must all occur. Longer words lose their last letter and match as a prefix, a crude
    stem for Slovak endings: 'nafta' finds nafty, naftou."""
    words = re.findall(r"\w+", fold(text))
    terms = [f'"{w[:-1]}"*' if len(w) >= 5 else f'"{w}"' for w in words]
    return " AND ".join(terms) or None


class VestnikIndex:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- writing ---------------------------------------------------------------------------------

    def issue_state(self) -> dict[tuple[int, int], str | None]:
        """{(year, number): dataset modified time} of every indexed issue."""
        return {(y, n): m for y, n, m in self.db.execute("SELECT year, number, modified FROM issues")}

    def add_issue(self, issue: dict, notices: list[dict], *, dataset: str | None = None, modified: str | None = None) -> None:
        """Store an issue's notices, replacing what an earlier version of the issue stored."""
        y, n = issue["year"], issue["number"]
        with self.db:
            old = [r[0] for r in self.db.execute("SELECT id FROM notices WHERE year=? AND number=?", (y, n))]
            old += [nt["id"] for nt in notices]  # the same notice stored under another issue
            self._delete(old)
            for notice in notices:
                award = notice.get("form_type") in AWARD_FORMS or (notice.get("kind") or "").endswith("Súhrnná správa")
                rowid = self.db.execute(
                    "INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (notice["id"], y, n, notice.get("published"), notice.get("kind"), notice.get("form_type"),
                     notice.get("notice_uuid"), int(award), _pack(summary(notice)), _pack(notice)),
                ).lastrowid
                rows = {(role, p["ico"]) for role, p in roles(notice) if p.get("ico")}
                self.db.executemany("INSERT INTO parties VALUES (?,?,?)", [(notice["id"], r, i) for r, i in rows])
                codes = set(notice.get("cpv", [])) | {c for lot in notice.get("lots", []) for c in lot.get("cpv", [])}
                self.db.executemany("INSERT INTO cpv VALUES (?,?)", [(notice["id"], c) for c in codes])
                # The text row shares the notice's rowid, so it can be deleted without scanning the table.
                self.db.execute("INSERT INTO search_text (rowid, id, text) VALUES (?,?,?)", (rowid, notice["id"], _search_text(notice)))
            self.db.execute(
                "INSERT OR REPLACE INTO issues VALUES (?,?,?,?,?,?,datetime('now'))",
                (y, n, issue.get("published"), dataset, modified, len(notices)),
            )

    def _delete(self, ids: list[str]) -> None:
        for nid in dict.fromkeys(ids):
            row = self.db.execute("SELECT rowid FROM notices WHERE id=?", (nid,)).fetchone()
            if row:
                self.db.execute("DELETE FROM search_text WHERE rowid=?", row)
                self.db.execute("DELETE FROM notices WHERE rowid=?", row)
            self.db.execute("DELETE FROM parties WHERE notice_id=?", (nid,))
            self.db.execute("DELETE FROM cpv WHERE notice_id=?", (nid,))

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    # --- reading ---------------------------------------------------------------------------------

    def coverage(self) -> dict | None:
        """Issues indexed and the publication dates they span."""
        count, first, last = self.db.execute("SELECT count(*), min(published), max(published) FROM issues").fetchone()
        return {"issues": count, "from": first, "to": last} if count else None

    def get(self, notice_id: str) -> dict | None:
        row = self.db.execute("SELECT detail FROM notices WHERE id=?", (notice_id,)).fetchone()
        return _unpack(row[0]) if row else None

    def roles_of(self, notice_ids: list[str], ico: str) -> dict[str, list[str]]:
        """{notice id: the roles an IČO has in it}."""
        if not notice_ids:
            return {}
        marks = ",".join("?" * len(notice_ids))
        out: dict[str, list[str]] = {}
        for nid, role in self.db.execute(
            f"SELECT notice_id, role FROM parties WHERE ico=? AND notice_id IN ({marks}) ORDER BY role", [ico, *notice_ids]
        ):
            out.setdefault(nid, []).append(role)
        return out

    def has_uuids(self, uuids: list[str]) -> set[str]:
        if not uuids:
            return set()
        marks = ",".join("?" * len(uuids))
        return {r[0] for r in self.db.execute(f"SELECT notice_uuid FROM notices WHERE notice_uuid IN ({marks})", uuids)}

    def search(
        self, *, icos: list[str] | None = None, role: str = "any", text: str | None = None, cpv_prefix: str | None = None,
        published_from: str | None = None, published_to: str | None = None, awards_only: bool = True,
        limit: int = 20, offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Summary rows, newest first, and the number of matches."""
        where, args = [], []
        if icos:
            marks = ",".join("?" * len(icos))
            where.append(f"id IN (SELECT notice_id FROM parties WHERE ico IN ({marks}) AND role IN {ROLES[role]})")
            args += icos
        if text and (q := fts_query(text)):
            where.append("id IN (SELECT id FROM search_text WHERE search_text MATCH ?)")
            args.append(q)
        if cpv_prefix:
            where.append("id IN (SELECT notice_id FROM cpv WHERE code LIKE ?)")
            args.append(cpv_prefix + "%")
        if published_from:
            where.append("published >= ?")
            args.append(published_from)
        if published_to:
            where.append("published <= ?")
            args.append(published_to)
        if awards_only:
            where.append("award = 1")
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.execute(f"SELECT count(*) FROM notices{sql_where}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT summary FROM notices{sql_where} ORDER BY published DESC, id DESC LIMIT ? OFFSET ?", [*args, limit, offset]
        ).fetchall()
        return [_unpack(r[0]) for r in rows], total
