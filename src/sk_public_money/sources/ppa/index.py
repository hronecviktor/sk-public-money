"""The local SQLite index of PPA's beneficiary lists: one row per beneficiary and financial year with
its totals by fund, its amounts per measure code, specific objectives and obec, and a full-text
index of the folded names (no case, no diacritics) and parent companies."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from ... import localdb
from ...text import fold
from ..rpo.tools import name_key
from .parse import FUNDS, place_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS years (
    year INTEGER PRIMARY KEY, url TEXT, published TEXT, indexed_at TEXT,
    beneficiaries INTEGER, operations INTEGER, total REAL
);
CREATE TABLE IF NOT EXISTS beneficiaries (
    id INTEGER PRIMARY KEY, year INTEGER, seq INTEGER, name TEXT, name_key TEXT, kind TEXT,
    places TEXT, parent TEXT, also_named TEXT, ico TEXT,
    epzf REAL, epfrv REAL, cofinancing REAL, total REAL, operations INTEGER
);
CREATE INDEX IF NOT EXISTS beneficiaries_total ON beneficiaries (year, total);
CREATE INDEX IF NOT EXISTS beneficiaries_key ON beneficiaries (name_key);
CREATE TABLE IF NOT EXISTS amounts (
    beneficiary INTEGER, year INTEGER, code TEXT, objectives TEXT, place TEXT, place_key TEXT,
    epzf REAL, epfrv REAL, cofinancing REAL, total REAL, operations INTEGER
);
CREATE INDEX IF NOT EXISTS amounts_beneficiary ON amounts (beneficiary);
CREATE INDEX IF NOT EXISTS amounts_code ON amounts (year, code);
CREATE INDEX IF NOT EXISTS amounts_place ON amounts (year, place_key);
CREATE VIRTUAL TABLE IF NOT EXISTS names USING fts5 (
    id UNINDEXED, name, parent, tokenize = 'unicode61 remove_diacritics 2'
);
"""


def default_path() -> Path:
    return localdb.db_path("ppa")


def fts_query(text: str) -> str | None:
    """Every word must occur, as a word prefix: 'kovac pet' finds Peter Kováč and Kováčová Petra."""
    words = re.findall(r"\w+", fold(text))
    return " AND ".join(f'"{w}"*' for w in words) or None


def _row(r: sqlite3.Row) -> dict:
    out = dict(r)
    for k in ("places", "parent", "also_named"):
        out[k] = json.loads(out[k]) if out.get(k) else None
    return out


class PPAIndex:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.db = localdb.connect(self.path, SCHEMA)

    def close(self) -> None:
        self.db.close()

    def get_meta(self, key: str) -> str | None:
        return localdb.get_meta(self.db, key)

    def set_meta(self, key: str, value: str) -> None:
        localdb.set_meta(self.db, key, value)

    # --- writing ---------------------------------------------------------------------------------

    def replace_year(self, year: int, beneficiaries: Iterable[dict], *, url: str | None = None, published: str | None = None) -> dict:
        """Index one financial year in a single transaction, replacing what was there."""
        count = ops = 0
        total = 0.0
        with self.db:
            self.delete_year(year, commit=False)
            for b in beneficiaries:
                cur = self.db.execute(
                    "INSERT INTO beneficiaries (year, seq, name, name_key, kind, places, parent, also_named, ico,"
                    " epzf, epfrv, cofinancing, total, operations) VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?,?)",
                    (year, b["seq"], b["name"], name_key(b["name"]), b["kind"],
                     json.dumps(b["places"], ensure_ascii=False), json.dumps(b["parent"], ensure_ascii=False) if b["parent"] else None,
                     json.dumps(b["also_named"], ensure_ascii=False) if b["also_named"] else None,
                     *(b[f] for f in FUNDS), b["total"], b["operations"]),
                )
                bid = cur.lastrowid
                self.db.executemany(
                    "INSERT INTO amounts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(bid, year, a["code"], ", ".join(a["objectives"]) or None, a["place"], place_key(a["place"]),
                      *(a[f] for f in FUNDS), a["total"], a["operations"]) for a in b["amounts"]],
                )
                names = " ".join([b["name"], *b["also_named"]])
                self.db.execute("INSERT INTO names (id, name, parent) VALUES (?,?,?)",
                                (bid, fold(names), fold(b["parent"]["name"]) if b["parent"] else ""))
                count += 1
                ops += b["operations"]
                total += b["total"]
            self.db.execute(
                "INSERT INTO years VALUES (?,?,?,?,?,?,?)",
                (year, url, published, time.strftime("%Y-%m-%dT%H:%M:%S"), count, ops, round(total, 2)),
            )
        return {"year": year, "beneficiaries": count, "operations": ops, "total": round(total, 2)}

    def delete_year(self, year: int, *, commit: bool = True) -> None:
        def run():
            self.db.execute("DELETE FROM names WHERE id IN (SELECT id FROM beneficiaries WHERE year = ?)", (year,))
            self.db.execute("DELETE FROM amounts WHERE year = ?", (year,))
            self.db.execute("DELETE FROM beneficiaries WHERE year = ?", (year,))
            self.db.execute("DELETE FROM years WHERE year = ?", (year,))
        if commit:
            with self.db:
                run()
        else:
            run()

    # --- reading ---------------------------------------------------------------------------------

    def years(self) -> dict[int, dict]:
        return {r["year"]: dict(r) for r in self.db.execute("SELECT * FROM years ORDER BY year")}

    def search(self, text: str, *, year: int | None = None, place: str | None = None, limit: int = 10, offset: int = 0) -> tuple[list[dict], int]:
        """Beneficiaries whose name (or parent company) has every word: the exact name first, then
        newest year and largest total first."""
        query = fts_query(text)
        if not query:
            raise ValueError("Give a name to search for.")
        where, args = ["b.id IN (SELECT id FROM names WHERE names MATCH ?)"], [query]
        self._filters(where, args, year=year, place=place)
        sql = " AND ".join(where)
        count = self.db.execute(f"SELECT COUNT(*) FROM beneficiaries b WHERE {sql}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT b.* FROM beneficiaries b WHERE {sql} ORDER BY b.name_key = ? DESC, b.year DESC, b.total DESC LIMIT ? OFFSET ?",
            [*args, name_key(text), limit, offset],
        ).fetchall()
        return [_row(r) for r in rows], count

    def by_name_keys(self, keys: list[str], *, year: int | None = None, place: str | None = None) -> list[dict]:
        """Beneficiaries whose name is one of these (as rpo.tools.name_key normalises them)."""
        where, args = [f"b.name_key IN ({','.join('?' * len(keys))})"], list(keys)
        self._filters(where, args, year=year, place=place)
        rows = self.db.execute(
            f"SELECT b.* FROM beneficiaries b WHERE {' AND '.join(where)} ORDER BY b.year DESC, b.total DESC", args
        ).fetchall()
        return [_row(r) for r in rows]

    def _filters(self, where: list[str], args: list, *, year: int | None, place: str | None) -> None:
        if year is not None:
            where.append("b.year = ?")
            args.append(year)
        if place:
            key = place_key(place)
            where.append("b.id IN (SELECT beneficiary FROM amounts WHERE place_key = ? OR place_key LIKE ?)")
            args += [key, key + "-%"]

    def amounts(self, ids: list[int]) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {i: [] for i in ids}
        if ids:
            for r in self.db.execute(
                f"SELECT * FROM amounts WHERE beneficiary IN ({','.join('?' * len(ids))}) ORDER BY rowid", ids
            ):
                out[r["beneficiary"]].append(dict(r))
        return out

    def top(self, year: int, *, code: str | None = None, place: str | None = None, kind: str | None = None,
            limit: int = 20, offset: int = 0) -> tuple[list[dict], dict]:
        """Beneficiaries ranked by what they received in a year, counting only the amounts under
        this measure code (or code group like 'V') and in this obec when given."""
        where, args = ["a.year = ?"], [year]
        if code:
            if "." in code:
                where.append("a.code = ?")
                args.append(code)
            else:
                where.append("a.code LIKE ?")
                args.append(code + ".%")
        if place:
            key = place_key(place)
            where.append("(a.place_key = ? OR a.place_key LIKE ?)")
            args += [key, key + "-%"]
        if kind:
            where.append("b.kind = ?")
            args.append(kind)
        sql = " AND ".join(where)
        sums = ", ".join(f"ROUND(SUM(a.{f}), 2) AS {f}" for f in (*FUNDS, "total"))
        grouped = (f"SELECT a.beneficiary AS id, {sums}, SUM(a.operations) AS ops FROM amounts a "
                   f"JOIN beneficiaries b ON b.id = a.beneficiary WHERE {sql} GROUP BY a.beneficiary")
        stats = self.db.execute(
            f"SELECT COUNT(*) AS beneficiaries, ROUND(SUM(total), 2) AS total, SUM(ops) AS operations FROM ({grouped})", args
        ).fetchone()
        rows = self.db.execute(
            f"SELECT g.epzf AS f_epzf, g.epfrv AS f_epfrv, g.cofinancing AS f_cofinancing, g.total AS f_total, b.* "
            f"FROM ({grouped}) g JOIN beneficiaries b ON b.id = g.id ORDER BY g.total DESC, b.name LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        out = []
        for r in rows:
            row = _row(r)
            row["filtered"] = {f: row.pop(f"f_{f}") for f in (*FUNDS, "total")}
            out.append(row)
        return out, dict(stats)
