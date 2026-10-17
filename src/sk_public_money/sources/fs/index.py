"""A local SQLite index of the Finančná správa lists, for lookups by IČO or DIČ and name searches.

The lists have no search API, only zipped XML files that are replaced daily, monthly or quarterly,
so sync.py downloads them, this module stores the fields the tools use, and the zips are deleted.
Amounts are stored in cents. One table per list, plus `subjects` (DIČ -> IČO, name, address) filled
from every list that names its taxpayers, so a row that carries only a DIČ can be found by IČO.
Corporate income tax accumulates over the quarterly files (each file's rows are kept, by list date);
every other list is replaced whole when a new version is indexed.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterable, Iterator
from itertools import islice
from pathlib import Path

from ... import localdb
from ...text import fold
from . import parse

SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (name TEXT PRIMARY KEY, as_of TEXT, last_modified TEXT, records INTEGER, skipped INTEGER, indexed_at REAL);
CREATE TABLE IF NOT EXISTS subjects (dic TEXT PRIMARY KEY, ico TEXT, name TEXT, place TEXT, rating INTEGER) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS subjects_ico ON subjects (ico);
CREATE INDEX IF NOT EXISTS subjects_less_reliable ON subjects (name) WHERE rating = 0;
CREATE TABLE IF NOT EXISTS vat (ic_dph TEXT PRIMARY KEY, ico TEXT, kind TEXT, registered TEXT, valid_from TEXT, kind_changed TEXT) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS vat_ico ON vat (ico);
CREATE TABLE IF NOT EXISTS vat_flags (list TEXT, ic_dph TEXT, ico TEXT, name TEXT, place TEXT, town TEXT, year TEXT, published TEXT, struck_off TEXT);
CREATE INDEX IF NOT EXISTS vat_flags_ico ON vat_flags (ico);
CREATE INDEX IF NOT EXISTS vat_flags_ic_dph ON vat_flags (ic_dph);
CREATE INDEX IF NOT EXISTS vat_flags_list ON vat_flags (list, published);
-- Dates as YYYYMMDD integers: this table holds several quarterly files.
CREATE TABLE IF NOT EXISTS cit (dic TEXT, period_from INTEGER, period_to INTEGER, tax INTEGER, additional INTEGER, loss INTEGER, release INTEGER,
    PRIMARY KEY (dic, period_from, period_to, release)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS vat_returns (dic TEXT, period TEXT, excess INTEGER, own INTEGER, PRIMARY KEY (dic, period)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS debtors (id INTEGER PRIMARY KEY, name TEXT, name_key TEXT, amount INTEGER, place TEXT, town TEXT);
CREATE INDEX IF NOT EXISTS debtors_key ON debtors (name_key);
CREATE VIRTUAL TABLE IF NOT EXISTS debtors_text USING fts5 (text, content = '', tokenize = 'unicode61 remove_diacritics 2');
"""

# List name -> (what it holds, how often Finančná správa replaces it in days).
LISTS = {
    "ds_dphs": ("VAT register (registrácia pre DPH)", 1),
    "ds_dphz": ("VAT payers with reasons to cancel their registration, § 52(6)", 1),
    "ds_dphv": ("VAT payers struck off, § 52(8)", 1),
    "ds_iz_ran": ("index daňovej spoľahlivosti", 1),
    "ds_dsdd": ("tax debtors (zoznam daňových dlžníkov)", 31),
    "ds_dppos": ("corporate income tax (daň z príjmov právnickej osoby)", 92),
    "ds_dphno": ("VAT excess deductions and own tax liability per period", 92),
}
FLAG_LISTS = {"ds_dphz": "cancellation_reasons", "ds_dphv": "struck_off"}
BATCH = 5000
GROUP_PREFIX = re.compile(r"^Skupina DPH zastupovan\w*:?\s*", re.I)
# The index daňovej spoľahlivosti, stored as a number in subjects.rating.
RATINGS = ["menej spoľahlivý", "spoľahlivý", "vysoko spoľahlivý"]
UPSERT_SUBJECT = """INSERT INTO subjects (dic, ico, name, place) VALUES (?,?,?,?) ON CONFLICT (dic) DO UPDATE SET
    ico = coalesce(excluded.ico, ico), name = coalesce(excluded.name, name), place = coalesce(excluded.place, place)"""
# An archived (older) file only fills in what no newer list gave.
FILL_SUBJECT = """INSERT INTO subjects (dic, ico, name, place) VALUES (?,?,?,?) ON CONFLICT (dic) DO UPDATE SET
    ico = coalesce(ico, excluded.ico), name = coalesce(name, excluded.name), place = coalesce(place, excluded.place)"""
# Every published row is kept: a later list can carry the same period again, e.g. with only the
# additional tax assessed after a supplementary return, which does not replace the first assessment.
UPSERT_CIT = "INSERT OR REPLACE INTO cit VALUES (?,?,?,?,?,?,?)"


def default_path() -> Path:
    return localdb.db_path("fs")


def _chunks(rows: Iterable, size: int = BATCH) -> Iterator[list]:
    it = iter(rows)
    while chunk := list(islice(it, size)):
        yield chunk


def _rated(item: dict) -> tuple | None:
    """(rating number, dic) for UPDATE subjects; None for a rating this module does not know."""
    row = parse.ids_row(item)
    return (RATINGS.index(row[1]), row[0]) if row and row[1] in RATINGS else None


def _day(value: str | None) -> int | None:
    """'2025-12-31' -> 20251231; None when the date did not parse."""
    return int(value.replace("-", "")) if value and len(value) == 10 and value[4] == "-" else None


def _iso(value: int | None) -> str | None:
    text = str(value) if value else ""
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 else None


def _cit(row: tuple | None) -> tuple | None:
    if row is None:
        return None
    dic, start, end, tax, additional, loss, release = row
    return (dic, _day(start), _day(end), tax, additional, loss, _day(release))


def fts_query(text: str) -> str | None:
    """Every word must occur, as a word prefix: 'Siroky' finds Široký, Širokého."""
    words = re.findall(r"\w+", fold(text))
    return " AND ".join(f'"{w}"*' for w in words) or None


class FSIndex:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.db = localdb.connect(self.path, SCHEMA)
        self.db.create_function("fold", 1, lambda s: fold(s) if s else s, deterministic=True)

    def close(self) -> None:
        self.db.close()

    def get_meta(self, key: str) -> str | None:
        return localdb.get_meta(self.db, key)

    def set_meta(self, key: str, value: str) -> None:
        localdb.set_meta(self.db, key, value)

    # --- writing ---------------------------------------------------------------------------------

    def load(self, name: str, path: Path, *, last_modified: str | None = None, archive: bool = False) -> dict:
        """Index one downloaded list file; returns {as_of, records, skipped}. archive: an older
        corporate tax file, which adds rows but does not stand for the list's current version."""
        source = parse.ListFile(path)
        counts = {"records": 0, "skipped": 0}

        def rows(mapper):
            for item in source:
                row = mapper(item)
                if row is None:
                    counts["skipped"] += 1
                    continue
                counts["records"] += 1
                yield row

        with self.db:
            if name == "ds_dppos":
                self._load_with_subjects(source, "NAZOV_DS", lambda item: _cit(parse.cit_row(item, source.as_of or "")), UPSERT_CIT, counts,
                                         subject_sql=FILL_SUBJECT if archive else UPSERT_SUBJECT)
            elif name == "ds_dphno":
                self.db.execute("DELETE FROM vat_returns")
                self._load_with_subjects(source, "NAZOV_DS", parse.vat_return_row, "INSERT OR REPLACE INTO vat_returns VALUES (?,?,?,?)", counts)
            elif name == "ds_iz_ran":
                self.db.execute("UPDATE subjects SET rating = NULL WHERE rating IS NOT NULL")
                self._load_with_subjects(source, "NAZOV_SUBJEKTU", _rated, "UPDATE subjects SET rating = ? WHERE dic = ?", counts)
            elif name == "ds_dphs":
                self.db.execute("DELETE FROM vat")
                self._load_with_subjects(source, "NAZOV_DS", parse.vat_row, "INSERT OR REPLACE INTO vat VALUES (?,?,?,?,?,?)", counts)
            elif name in FLAG_LISTS:
                self.db.execute("DELETE FROM vat_flags WHERE list = ?", (FLAG_LISTS[name],))
                for chunk in _chunks(rows(lambda item: parse.flag_row(item, FLAG_LISTS[name]))):
                    self.db.executemany("INSERT INTO vat_flags VALUES (?,?,?,?,?,?,?,?,?)", chunk)
            elif name == "ds_dsdd":
                self.db.execute("DELETE FROM debtors")
                self.db.execute("INSERT INTO debtors_text (debtors_text) VALUES ('delete-all')")
                for chunk in _chunks(rows(parse.debtor_row)):
                    for row in chunk:
                        rowid = self.db.execute("INSERT INTO debtors (name, name_key, amount, place, town) VALUES (?,?,?,?,?)", row).lastrowid
                        # A contentless full-text table: it only finds rowids, the text stays in debtors.
                        self.db.execute("INSERT INTO debtors_text (rowid, text) VALUES (?,?)", (rowid, fold(row[0])))
            else:
                raise ValueError(f"Unknown list {name}")
            if not archive:
                self.db.execute(
                    "INSERT OR REPLACE INTO lists VALUES (?,?,?,?,?,?)",
                    (name, source.as_of, last_modified, counts["records"], counts["skipped"], time.time()),
                )
        return {"as_of": source.as_of, **counts}

    def _load_with_subjects(self, source: parse.ListFile, name_field: str, mapper, sql: str, counts: dict,
                            subject_sql: str = UPSERT_SUBJECT) -> None:
        for chunk in _chunks(source):
            subjects, rows = [], []
            for item in chunk:
                row = mapper(item)
                if row is None:
                    counts["skipped"] += 1
                    continue
                counts["records"] += 1
                rows.append(row)
                if (s := parse.subject(item, name_field)) is not None:
                    subjects.append(s)
            self.db.executemany(subject_sql, subjects)
            self.db.executemany(sql, rows)

    # --- reading ---------------------------------------------------------------------------------

    def lists(self) -> dict[str, dict]:
        return {r["name"]: dict(r) for r in self.db.execute("SELECT * FROM lists")}

    def is_built(self) -> bool:
        return bool(self.db.execute("SELECT 1 FROM lists LIMIT 1").fetchone())

    def as_of(self) -> dict[str, str | None]:
        return {name: info["as_of"] for name, info in sorted(self.lists().items())}

    def cit_coverage(self) -> dict | None:
        row = self.db.execute("SELECT min(period_to), max(period_to), min(release) FROM cit").fetchone()
        return {"periods_to": [_iso(row[0]), _iso(row[1])], "oldest_list": _iso(row[2])} if row[0] else None

    def dics_for_ico(self, ico: str) -> list[str]:
        dics = [r[0] for r in self.db.execute("SELECT dic FROM subjects WHERE ico = ? ORDER BY dic", (ico,))]
        dics += [d for (v,) in self.db.execute("SELECT ic_dph FROM vat WHERE ico = ?", (ico,)) if (d := parse.dic_of(v))]
        return list(dict.fromkeys(dics))

    def subject(self, dic: str) -> dict | None:
        row = self.db.execute("SELECT * FROM subjects WHERE dic = ?", (dic,)).fetchone()
        if not row:
            return None
        return {**dict(row), "rating": RATINGS[row["rating"]] if row["rating"] is not None else None}


    def vat_registrations(self, ico: str | None, dics: list[str]) -> list[dict]:
        marks = ",".join("?" * len(dics)) or "NULL"
        rows = self.db.execute(f"SELECT * FROM vat WHERE ico = ? OR ic_dph IN ({marks}) ORDER BY registered", [ico, *(f"SK{d}" for d in dics)])
        return [dict(r) for r in rows]

    def vat_flags(self, ico: str | None, ic_dphs: list[str]) -> list[dict]:
        marks = ",".join("?" * len(ic_dphs)) or "NULL"
        rows = self.db.execute(
            f"SELECT * FROM vat_flags WHERE ico = ? OR ic_dph IN ({marks}) ORDER BY published DESC", [ico, *ic_dphs]
        )
        return [dict(r) for r in rows]

    def cit(self, dics: list[str]) -> list[dict]:
        marks = ",".join("?" * len(dics))
        rows = self.db.execute(f"SELECT * FROM cit WHERE dic IN ({marks}) ORDER BY period_to DESC, release DESC, dic", dics)
        return [{**dict(r), **{k: _iso(r[k]) for k in ("period_from", "period_to", "release")}} for r in rows]

    def vat_returns(self, dics: list[str]) -> list[dict]:
        marks = ",".join("?" * len(dics))
        rows = self.db.execute(f"SELECT * FROM vat_returns WHERE dic IN ({marks})", dics)
        return [dict(r) for r in rows]

    def vat_groups_led_by(self, names: list[str]) -> list[dict]:
        """VAT groups (named 'Skupina DPH zastupovaná: <representative>') whose representative has one
        of these names."""
        keys = {parse.name_key(n) for n in names} - {""}
        out = []
        for row in self.db.execute("SELECT dic, name FROM subjects WHERE name LIKE 'Skupina DPH zastupovan%'"):
            if parse.name_key(GROUP_PREFIX.sub("", row["name"])) in keys:
                out.append(dict(row))
        return out

    def debtors_named(self, names: list[str]) -> list[dict]:
        keys = [k for k in dict.fromkeys(parse.name_key(n) for n in names) if k]
        if not keys:
            return []
        marks = ",".join("?" * len(keys))
        return [dict(r) for r in self.db.execute(f"SELECT name, amount, place FROM debtors WHERE name_key IN ({marks})", keys)]

    def search_debtors(self, name: str, *, town: str | None, min_amount: int | None, limit: int, offset: int) -> tuple[list[dict], int]:
        q = fts_query(name)
        if not q:
            raise ValueError("Give at least one word of the name.")
        where, args = ["id IN (SELECT rowid FROM debtors_text WHERE debtors_text MATCH ?)"], [q]
        if town:
            where.append("fold(place) LIKE ?")
            args.append(f"%{fold(town)}%")
        if min_amount is not None:
            where.append("amount >= ?")
            args.append(min_amount)
        sql = " WHERE " + " AND ".join(where)
        total = self.db.execute(f"SELECT count(*) FROM debtors{sql}", args).fetchone()[0]
        rows = self.db.execute(f"SELECT name, amount, place FROM debtors{sql} ORDER BY amount DESC LIMIT ? OFFSET ?", [*args, limit, offset])
        return [dict(r) for r in rows], total

    def flagged(
        self, kind: str, *, town: str | None, date_from: str | None, date_to: str | None, limit: int, offset: int,
    ) -> tuple[list[dict], int]:
        """Rows of one flag list: cancellation_reasons, struck_off (newest first) or less_reliable."""
        where, args = [], []
        if kind == "less_reliable":
            base = "FROM subjects WHERE rating = 0"
            if town:
                where.append("fold(place) LIKE ?")
                args.append(f"%{fold(town)}%")
            sql = base + "".join(f" AND {w}" for w in where)
            total = self.db.execute(f"SELECT count(*) {sql}", args).fetchone()[0]
            rows = self.db.execute(
                f"SELECT ico, dic, name, place {sql} ORDER BY name LIMIT ? OFFSET ?", [*args, limit, offset]
            )
            return [dict(r) for r in rows], total
        column = "struck_off" if kind == "struck_off" else "published"
        where, args = ["list = ?"], [kind]
        if town:
            where.append("fold(place) LIKE ?")
            args.append(f"%{fold(town)}%")
        if date_from:
            where.append(f"{column} >= ?")
            args.append(date_from)
        if date_to:
            where.append(f"{column} <= ?")
            args.append(date_to)
        sql = " WHERE " + " AND ".join(where)
        total = self.db.execute(f"SELECT count(*) FROM vat_flags{sql}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT ico, ic_dph, name, place, year, published, struck_off FROM vat_flags{sql} ORDER BY {column} DESC, name LIMIT ? OFFSET ?",
            [*args, limit, offset],
        )
        return [dict(r) for r in rows], total
