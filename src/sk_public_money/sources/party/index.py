"""A local SQLite index of the parties' annual reports (výročné správy) that minv.sk lists.

The listing comes from one index page per year (sync.py): party, year, PDF address, whether the file
is a corrected version and whether the commission marked it as deficient. What is inside a report
(how many pages, which carry a real text layer, which are scans, which pages name the registers) is
known only once the PDF has been read, so party_read_report fills it in the first time and it is kept
here for later calls. The PDFs themselves are not kept.
"""

from __future__ import annotations

import json
import time
import zlib
from pathlib import Path

from ... import localdb
from ...text import fold

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, year INTEGER, party TEXT, party_folded TEXT, file TEXT, url TEXT,
    corrected INTEGER, deficient INTEGER, listing_note TEXT, listed_at REAL,
    pages INTEGER, coverage BLOB, read_at REAL
);
CREATE INDEX IF NOT EXISTS reports_year ON reports (year);
CREATE TABLE IF NOT EXISTS years (year INTEGER PRIMARY KEY, url TEXT, reports INTEGER, notes TEXT, listed_at REAL);
"""


def default_path() -> Path:
    return localdb.db_path("party", "reports")


def report_id(year: int, file: str) -> str:
    return f"{year}/{file}"


def _pack(value) -> bytes:
    return zlib.compress(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), 6)


def _unpack(blob: bytes | None):
    return json.loads(zlib.decompress(blob)) if blob else None


def _stem(file: str) -> str:
    """'VS24r_SMER_f.pdf' -> 'smer': the party's short name in the file name, when it has one."""
    stem = file.rsplit(".", 1)[0]
    stem = stem.split("_", 1)[1] if stem.upper().startswith("VS") and "_" in stem else stem
    return fold(stem.removesuffix("_f").replace("_", " ")).strip()


class ReportIndex:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.db = localdb.connect(self.path, SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- writing ---------------------------------------------------------------------------------

    def replace_year(self, year: int, url: str, listed: list[dict], notes: dict) -> dict:
        """Store a year's listing. A report whose PDF address is unchanged keeps what was read from
        it; reports no longer listed are dropped. Returns counts of added, kept and dropped reports."""
        now = time.time()
        old = {r["id"]: r["url"] for r in self.db.execute("SELECT id, url FROM reports WHERE year=?", (year,))}
        new_ids = set()
        added = 0
        with self.db:
            for rep in listed:
                rid = report_id(year, rep["file"])
                new_ids.add(rid)
                same = old.get(rid) == rep["url"]
                added += rid not in old
                self.db.execute(
                    "INSERT INTO reports (id, year, party, party_folded, file, url, corrected, deficient, listing_note, listed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET party=excluded.party, "
                    "party_folded=excluded.party_folded, url=excluded.url, corrected=excluded.corrected, "
                    "deficient=excluded.deficient, listing_note=excluded.listing_note, listed_at=excluded.listed_at"
                    + ("" if same else ", pages=NULL, coverage=NULL, read_at=NULL"),
                    (rid, year, rep["party"], fold(rep["party"]), rep["file"], rep["url"], int(rep.get("corrected", False)),
                     int(rep.get("deficient", False)), rep.get("listing_note"), now),
                )
            dropped = [rid for rid in old if rid not in new_ids]
            self.db.executemany("DELETE FROM reports WHERE id=?", [(rid,) for rid in dropped])
            self.db.execute(
                "INSERT OR REPLACE INTO years VALUES (?,?,?,?,?)",
                (year, url, len(listed), json.dumps(notes, ensure_ascii=False), now),
            )
        return {"added": added, "kept": len(new_ids) - added, "dropped": len(dropped)}

    def set_coverage(self, rid: str, pages: int, coverage: dict) -> None:
        with self.db:
            self.db.execute("UPDATE reports SET pages=?, coverage=?, read_at=? WHERE id=?", (pages, _pack(coverage), time.time(), rid))

    def get_meta(self, key: str) -> str | None:
        return localdb.get_meta(self.db, key)

    def set_meta(self, key: str, value: str) -> None:
        localdb.set_meta(self.db, key, value)

    # --- reading ---------------------------------------------------------------------------------

    def years(self) -> dict[int, dict]:
        """{year: {reports, notes, listed_at}} of every listed year."""
        return {
            r["year"]: {"reports": r["reports"], "notes": json.loads(r["notes"] or "{}"), "listed_at": r["listed_at"]}
            for r in self.db.execute("SELECT * FROM years ORDER BY year")
        }

    def oldest_listing(self) -> float | None:
        row = self.db.execute("SELECT min(listed_at) FROM years").fetchone()
        return row[0] if row else None

    def find(self, *, party: str | None = None, year: int | None = None) -> list[dict]:
        """Reports of a year and/or party (all words of the party name or its short file name, any
        case and diacritics), newest year first."""
        sql, args = "SELECT * FROM reports", []
        if year:
            sql += " WHERE year = ?"
            args.append(year)
        rows = self.db.execute(sql + " ORDER BY year DESC, party_folded", args).fetchall()
        if party:
            words = fold(party).split()
            wanted = " ".join(words)
            rows = [r for r in rows if all(w in r["party_folded"] for w in words) or _stem(r["file"]) == wanted]
        return [self.record(r) for r in rows]

    @staticmethod
    def record(r) -> dict:
        out = {"id": r["id"], "year": r["year"], "party": r["party"], "url": r["url"]}
        if r["corrected"]:
            out["corrected_version"] = True
        if r["deficient"]:
            out["deficient"] = True
        if r["listing_note"]:
            out["listing_note"] = r["listing_note"]
        if r["coverage"]:
            out["coverage"] = _unpack(r["coverage"])
        return out

    def get(self, rid: str) -> dict | None:
        row = self.db.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        return self.record(row) if row else None
