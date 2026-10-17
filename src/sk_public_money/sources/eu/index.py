"""A local SQLite index of ITMS21+, for searching EU-funded projects and their procurements by IČO.

The API filters projects and claims only by its own subject IDs and procurements not at all by
supplier, so its lists are loaded whole (sync.py) and kept here as compact rows: organisations
(the subjekt register and the separate dodavatelobstaravatel register of suppliers), programmes and
calls, projects and grant applications with their parties, payment claims, procurements with the
details that link them to projects and contracts, and invoices. Full text (no case, no diacritics)
covers names and codes of projects, applications, procurements and their organisations.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import zlib
from pathlib import Path

from ... import localdb
from ...text import fold

SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects (id INTEGER PRIMARY KEY, ico TEXT, name TEXT, data BLOB);
CREATE INDEX IF NOT EXISTS subjects_ico ON subjects (ico);
CREATE TABLE IF NOT EXISTS suppliers (id INTEGER PRIMARY KEY, ico TEXT, name TEXT, data BLOB, fetched REAL);
CREATE INDEX IF NOT EXISTS suppliers_ico ON suppliers (ico);
CREATE TABLE IF NOT EXISTS programmes (id INTEGER PRIMARY KEY, code TEXT, abbr TEXT, name TEXT);
CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, code TEXT, name TEXT, programme_id INTEGER);
CREATE TABLE IF NOT EXISTS items (
    rowid INTEGER PRIMARY KEY, kind TEXT, id INTEGER, code TEXT, programme_id INTEGER, org_id INTEGER,
    amount REAL, project_id INTEGER, data BLOB, UNIQUE (kind, id)
);
CREATE INDEX IF NOT EXISTS items_code ON items (code);
CREATE TABLE IF NOT EXISTS parties (kind TEXT, item_id INTEGER, role TEXT, subject_id INTEGER);
CREATE INDEX IF NOT EXISTS parties_subject ON parties (subject_id, role);
CREATE INDEX IF NOT EXISTS parties_item ON parties (kind, item_id);
CREATE TABLE IF NOT EXISTS claims (id INTEGER PRIMARY KEY, project_id INTEGER, amount REAL, paid INTEGER, data BLOB);
CREATE INDEX IF NOT EXISTS claims_project ON claims (project_id);
CREATE TABLE IF NOT EXISTS procurements (
    id INTEGER PRIMARY KEY, code TEXT, buyer_id INTEGER, buyer_supplier_id INTEGER, updated_ms INTEGER,
    data BLOB, detail BLOB, detail_updated_ms INTEGER, detail_fetched REAL
);
CREATE INDEX IF NOT EXISTS procurements_buyer ON procurements (buyer_id);
CREATE INDEX IF NOT EXISTS procurements_code ON procurements (code);
CREATE TABLE IF NOT EXISTS procurement_projects (procurement_id INTEGER, project_id INTEGER);
CREATE INDEX IF NOT EXISTS pp_project ON procurement_projects (project_id);
CREATE INDEX IF NOT EXISTS pp_procurement ON procurement_projects (procurement_id);
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY, procurement_id INTEGER, supplier_id INTEGER, supplier_subject_id INTEGER, amount REAL, data BLOB
);
CREATE INDEX IF NOT EXISTS contracts_procurement ON contracts (procurement_id);
CREATE INDEX IF NOT EXISTS contracts_supplier ON contracts (supplier_id);
CREATE INDEX IF NOT EXISTS contracts_supplier_subject ON contracts (supplier_subject_id);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY, owner_id INTEGER, supplier_subject_id INTEGER, supplier_id INTEGER, amount REAL, data BLOB
);
CREATE INDEX IF NOT EXISTS invoices_supplier_subject ON invoices (supplier_subject_id);
CREATE INDEX IF NOT EXISTS invoices_supplier ON invoices (supplier_id);
CREATE VIRTUAL TABLE IF NOT EXISTS items_text USING fts5 (text, tokenize = 'unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE IF NOT EXISTS procurements_text USING fts5 (text, tokenize = 'unicode61 remove_diacritics 2');
"""
# Search roles -> party roles stored for projects and applications.
ITEM_ROLES = {"any": ("recipient", "partner"), "recipient": ("recipient",), "partner": ("partner",)}


def default_path() -> Path:
    return localdb.db_path("eu", "itms21")


def pack(value) -> bytes:
    return zlib.compress(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), 6)


def unpack(blob: bytes | None):
    return json.loads(zlib.decompress(blob)) if blob else None


def fts_query(text: str) -> str | None:
    """Words that must all occur. Words of five or more letters lose their last letter and match as a
    prefix, a crude stem for Slovak endings: 'cesta' finds cesty, cestou."""
    words = re.findall(r"\w+", fold(text))
    terms = [f'"{w[:-1]}"*' if len(w) >= 5 else f'"{w}"' for w in words]
    return " AND ".join(terms) or None


def _marks(values) -> str:
    return ",".join("?" * len(values))


class EUIndex:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.db = localdb.connect(self.path, SCHEMA)
        self.db.row_factory = None

    def close(self) -> None:
        self.db.close()

    def get_meta(self, key: str) -> str | None:
        return localdb.get_meta(self.db, key)

    def set_meta(self, key: str, value: str) -> None:
        localdb.set_meta(self.db, key, value)

    # --- writing: each list is replaced as a whole in one transaction -----------------------------

    def replace_subjects(self, rows: list[tuple[int, dict]]) -> None:
        with self.db:
            self.db.execute("DELETE FROM subjects")
            self.db.executemany(
                "INSERT INTO subjects VALUES (?,?,?,?)", [(i, o.get("ico"), o.get("name"), pack(o)) for i, o in rows]
            )

    def add_supplier(self, supplier_id: int, org: dict | None) -> None:
        """Cache a dodavatelobstaravatel record; None records an ID the API does not know."""
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO suppliers VALUES (?,?,?,?,?)",
                (supplier_id, (org or {}).get("ico"), (org or {}).get("name"), pack(org) if org else None, time.time()),
            )

    def replace_programmes(self, programmes: list[dict], calls: list[dict]) -> None:
        with self.db:
            self.db.execute("DELETE FROM programmes")
            self.db.execute("DELETE FROM calls")
            self.db.executemany("INSERT INTO programmes VALUES (:id,:code,:abbr,:name)", programmes)
            self.db.executemany("INSERT INTO calls VALUES (:id,:code,:name,:programme_id)", calls)

    def replace_items(self, projects: list[dict], applications: list[dict]) -> None:
        """Projects and grant applications, their parties and their full-text rows."""
        calls = dict(self.db.execute("SELECT id, programme_id FROM calls"))
        programmes = {code: pid for pid, code in self.db.execute("SELECT id, code FROM programmes")}
        names = dict(self.db.execute("SELECT id, name FROM subjects"))
        with_project = {p["application_id"]: p["id"] for p in projects if p.get("application_id")}
        with self.db:
            for table in ("items", "parties", "items_text"):
                self.db.execute(f"DELETE FROM {table}")
            for kind, rows in (("project", projects), ("application", applications)):
                for row in rows:
                    org = row.get("recipient_id") if kind == "project" else row.get("applicant_id")
                    programme = calls.get(row.get("call_id")) or self._programme_by_code(row.get("code"), programmes)
                    amount = row.get("contracted") if kind == "project" else row.get("approved_grant") or row.get("requested_grant")
                    project_id = row["id"] if kind == "project" else with_project.get(row["id"])
                    rowid = self.db.execute(
                        "INSERT INTO items (kind, id, code, programme_id, org_id, amount, project_id, data) VALUES (?,?,?,?,?,?,?,?)",
                        (kind, row["id"], row.get("code"), programme, org, amount, project_id, pack(row)),
                    ).lastrowid
                    parties = [("recipient", org)] + [("partner", p) for p in row.get("partner_ids", [])]
                    self.db.executemany(
                        "INSERT INTO parties VALUES (?,?,?,?)", [(kind, row["id"], role, s) for role, s in parties if s]
                    )
                    text = " ".join([row.get("code") or "", row.get("name") or ""] + [names.get(s) or "" for _, s in parties])
                    self.db.execute("INSERT INTO items_text (rowid, text) VALUES (?,?)", (rowid, fold(text)))

    @staticmethod
    def _programme_by_code(code: str | None, programmes: dict[str, int]) -> int | None:
        """Project and application codes start with their programme's code (401501F545 -> 401000;
        the home-affairs funds 408010-408030 differ only in the first six digits)."""
        code = re.sub(r"^\D+", "", code or "")
        return programmes.get(code[:6]) or programmes.get(code[:3] + "000")

    def replace_claims(self, claims: list[dict]) -> None:
        with self.db:
            self.db.execute("DELETE FROM claims")
            self.db.executemany(
                "INSERT INTO claims VALUES (?,?,?,?,?)",
                [(c["id"], c.get("project_id"), c.get("amount"), int(bool(c.get("paid"))), pack(c)) for c in claims],
            )

    def replace_invoices(self, invoices: list[dict]) -> None:
        with self.db:
            self.db.execute("DELETE FROM invoices")
            self.db.executemany(
                "INSERT INTO invoices VALUES (?,?,?,?,?,?)",
                [(i["id"], i.get("owner_id"), i.get("supplier_subject_id"), i.get("supplier_id"), i.get("amount"), pack(i))
                 for i in invoices],
            )

    def replace_procurements(self, rows: list[dict]) -> None:
        """Procurement list records. Details fetched earlier are kept for procurements still listed;
        the rest (and their contracts and project links) are dropped."""
        keep = {r["id"] for r in rows}
        with self.db:
            gone = [i for (i,) in self.db.execute("SELECT id FROM procurements") if i not in keep]
            for chunk in range(0, len(gone), 500):
                ids = gone[chunk : chunk + 500]
                for table, col in (("procurements", "id"), ("procurement_projects", "procurement_id"), ("contracts", "procurement_id")):
                    self.db.execute(f"DELETE FROM {table} WHERE {col} IN ({_marks(ids)})", ids)
            for r in rows:
                self.db.execute(
                    "INSERT INTO procurements (id, code, buyer_id, buyer_supplier_id, updated_ms, data) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT (id) DO UPDATE SET code=excluded.code, buyer_id=excluded.buyer_id, "
                    "buyer_supplier_id=excluded.buyer_supplier_id, updated_ms=excluded.updated_ms, data=excluded.data",
                    (r["id"], r.get("code"), r.get("buyer_id"), r.get("buyer_supplier_id"), r.get("updated_ms"), pack(r)),
                )
        self.rebuild_procurement_text()

    def add_procurement_detail(self, procurement_id: int, detail: dict, updated_ms: int | None) -> None:
        contracts = detail.pop("contracts", [])
        with self.db:
            self.db.execute(
                "UPDATE procurements SET detail=?, detail_updated_ms=?, detail_fetched=? WHERE id=?",
                (pack(detail), updated_ms, time.time(), procurement_id),
            )
            self.db.execute("DELETE FROM procurement_projects WHERE procurement_id=?", (procurement_id,))
            self.db.executemany(
                "INSERT INTO procurement_projects VALUES (?,?)", [(procurement_id, p) for p in detail.get("project_ids", [])]
            )
            self.db.execute("DELETE FROM contracts WHERE procurement_id=?", (procurement_id,))
            self.db.executemany(
                "INSERT INTO contracts VALUES (?,?,?,?,?,?)",
                [(c["id"], procurement_id, c.get("supplier_id"), c.get("supplier_subject_id"), c.get("amount"), pack(c)) for c in contracts],
            )

    def rebuild_procurement_text(self) -> None:
        """Full text of procurements: code, name, buyer and the main suppliers of their contracts."""
        subjects = dict(self.db.execute("SELECT id, name FROM subjects"))
        suppliers = dict(self.db.execute("SELECT id, name FROM suppliers WHERE name IS NOT NULL"))
        sups: dict[int, list[str]] = {}
        for pid, sid, ssid in self.db.execute("SELECT procurement_id, supplier_id, supplier_subject_id FROM contracts"):
            sups.setdefault(pid, []).append(suppliers.get(sid) or subjects.get(ssid) or "")
        with self.db:
            self.db.execute("DELETE FROM procurements_text")
            for pid, code, buyer, buyer_s, blob in self.db.execute(
                "SELECT id, code, buyer_id, buyer_supplier_id, data FROM procurements"
            ).fetchall():
                name = unpack(blob).get("name") or ""
                text = " ".join([code or "", name, subjects.get(buyer) or suppliers.get(buyer_s) or ""] + sups.get(pid, []))
                self.db.execute("INSERT INTO procurements_text (rowid, text) VALUES (?,?)", (pid, fold(text)))

    # --- sync bookkeeping ------------------------------------------------------------------------

    def procurements_due(self, *, open_age: float, closed_age: float, final: tuple[str, ...]) -> list[tuple[int, int | None]]:
        """(id, list updatedAt) of procurements whose detail is missing, older than their list record,
        or older than open_age (closed_age for final statuses: contracts can be added without the
        procurement's updatedAt changing). Never-fetched first, then oldest."""
        now = time.time()
        out = []
        for pid, updated, blob, d_updated, fetched in self.db.execute(
            "SELECT id, updated_ms, data, detail_updated_ms, detail_fetched FROM procurements ORDER BY detail_fetched IS NOT NULL, detail_fetched, id"
        ):
            status = unpack(blob).get("status")
            age = closed_age if status in final else open_age
            if fetched is None or (updated or 0) > (d_updated or 0) or now - fetched > age:
                out.append((pid, updated))
        return out

    def unknown_suppliers(self) -> list[int]:
        """dodavatelobstaravatel IDs referenced by contracts, buyers and invoices but not cached yet;
        contract suppliers first."""
        known = {i for (i,) in self.db.execute("SELECT id FROM suppliers")}
        out: dict[int, None] = {}
        for sql in (
            "SELECT DISTINCT supplier_id FROM contracts",
            "SELECT DISTINCT buyer_supplier_id FROM procurements",
            "SELECT DISTINCT supplier_id FROM invoices",
        ):
            for (i,) in self.db.execute(sql):
                if i is not None and i not in known:
                    out[i] = None
        return list(out)

    def counts(self) -> dict:
        q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "projects": q("SELECT count(*) FROM items WHERE kind='project'"),
            "applications": q("SELECT count(*) FROM items WHERE kind='application'"),
            "procurements": q("SELECT count(*) FROM procurements"),
            "procurements_with_detail": q("SELECT count(*) FROM procurements WHERE detail IS NOT NULL"),
            "contracts": q("SELECT count(*) FROM contracts"),
            "payment_claims": q("SELECT count(*) FROM claims"),
            "invoices": q("SELECT count(*) FROM invoices"),
            "organisations": q("SELECT count(*) FROM subjects"),
            "suppliers": q("SELECT count(*) FROM suppliers"),
        }

    def coverage(self) -> dict | None:
        """When the lists were loaded and how complete the procurement details are."""
        synced = self.get_meta("lists_synced_at")
        if not synced:
            return None
        c = self.counts()
        out = {"lists_loaded": time.strftime("%Y-%m-%d %H:%M", time.localtime(float(synced)))}
        if c["procurements_with_detail"] < c["procurements"]:
            out["procurement_details"] = f"{c['procurements_with_detail']} of {c['procurements']} procurements"
        return out

    # --- reading ---------------------------------------------------------------------------------

    def subject_ids(self, ico: str) -> list[int]:
        return [i for (i,) in self.db.execute("SELECT id FROM subjects WHERE ico=?", (ico,))]

    def supplier_ids(self, ico: str) -> list[int]:
        return [i for (i,) in self.db.execute("SELECT id FROM suppliers WHERE ico=?", (ico,))]

    def org(self, subject_id: int | None) -> dict | None:
        if subject_id is None:
            return None
        row = self.db.execute("SELECT data FROM subjects WHERE id=?", (subject_id,)).fetchone()
        return unpack(row[0]) if row else {"subject_id": subject_id, "note": "not in the subjekt list"}

    def supplier(self, supplier_id: int | None) -> dict | None:
        if supplier_id is None:
            return None
        row = self.db.execute("SELECT data, fetched FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if row and row[0]:
            return unpack(row[0])
        return {"supplier_id": supplier_id, "note": "not resolved yet (run sk-public-money-sync eu)" if not row else "unknown to the API"}

    def short_org(self, subject_id: int | None = None, supplier_id: int | None = None) -> dict | None:
        o = self.org(subject_id) if subject_id is not None else self.supplier(supplier_id)
        return {k: v for k, v in o.items() if k in ("name", "ico", "other_id", "note", "subject_id", "supplier_id")} if o else None

    def programmes(self) -> list[dict]:
        return [{"id": i, "code": c, "abbr": a, "name": n} for i, c, a, n in self.db.execute("SELECT id, code, abbr, name FROM programmes ORDER BY code")]

    def programme(self, programme_id: int | None) -> str | None:
        row = self.db.execute("SELECT abbr, name FROM programmes WHERE id=?", (programme_id,)).fetchone()
        return f"{row[0]}: {row[1]}" if row else None

    def call(self, call_id: int | None) -> dict | None:
        row = self.db.execute("SELECT code, name FROM calls WHERE id=?", (call_id,)).fetchone()
        return {"code": row[0], "name": row[1]} if row else None

    def find_programme(self, text: str) -> int:
        """A programme by ID, code or abbreviation (PSK, PRH SR, VI A SKAT ...), a unique part of the
        abbreviation (SKAT, PRH) or a unique part of the name."""
        want = fold(text).replace(" ", "").replace("-", "")
        programmes = self.programmes()
        abbr = lambda p: fold(p["abbr"] or "").replace(" ", "").replace("-", "")  # noqa: E731
        for p in programmes:
            if want in (abbr(p), p["code"], str(p["id"])):
                return p["id"]
        for matches in ([p for p in programmes if want and want in abbr(p)], [p for p in programmes if fold(text) in fold(p["name"])]):
            if len(matches) == 1:
                return matches[0]["id"]
        names = ", ".join(f"{p['abbr']} ({p['name']})" for p in programmes)
        raise ValueError(f"Unknown or ambiguous programme {text!r}; ITMS21+ has: {names}.")

    def claims_summary(self, project_ids: list[int]) -> dict[int, dict]:
        if not project_ids:
            return {}
        out = {}
        for pid, n, total, paid, paid_n in self.db.execute(
            f"SELECT project_id, count(*), sum(amount), sum(CASE WHEN paid THEN amount END), sum(paid) FROM claims "
            f"WHERE project_id IN ({_marks(project_ids)}) GROUP BY project_id", project_ids,
        ):
            out[pid] = {"count": n, "claimed": round(total or 0, 2), "claimed_in_paid_claims": round(paid or 0, 2), "paid_claims": paid_n}
        return out

    def claims(self, project_id: int) -> list[dict]:
        return [unpack(b) for (b,) in self.db.execute("SELECT data FROM claims WHERE project_id=? ORDER BY id", (project_id,))]

    def item(self, kind: str, item_id: int) -> dict | None:
        row = self.db.execute("SELECT data, programme_id FROM items WHERE kind=? AND id=?", (kind, item_id)).fetchone()
        return {**unpack(row[0]), "programme_id": row[1]} if row else None

    def project_id_by_code(self, code: str) -> int | None:
        row = self.db.execute("SELECT id FROM items WHERE kind='project' AND code=?", (code,)).fetchone()
        return row[0] if row else None

    def search_items(
        self, *, subject_ids: list[int] | None, role: str, text: str | None, programme_id: int | None,
        kind: str, sort: str, limit: int, offset: int,
    ) -> tuple[list[dict], int]:
        where, args = [], []
        if kind == "projects":
            where.append("kind = 'project'")
        elif kind == "applications":
            where.append("kind = 'application'")
        else:
            # An application that became a project is shown as the project.
            where.append("(kind = 'project' OR project_id IS NULL)")
        if subject_ids is not None:
            roles = ITEM_ROLES[role]
            where.append(
                f"rowid IN (SELECT i.rowid FROM parties p JOIN items i ON i.kind=p.kind AND i.id=p.item_id "
                f"WHERE p.subject_id IN ({_marks(subject_ids)}) AND p.role IN ({_marks(roles)}))"
            )
            args += [*subject_ids, *roles]
        if text and (q := fts_query(text)):
            where.append("rowid IN (SELECT rowid FROM items_text WHERE items_text MATCH ?)")
            args.append(q)
        if programme_id is not None:
            where.append("programme_id = ?")
            args.append(programme_id)
        sql_where = " WHERE " + " AND ".join(where)
        order = "amount IS NULL, amount DESC, id DESC" if sort == "amount" else "id DESC"
        total = self.db.execute(f"SELECT count(*) FROM items{sql_where}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT kind, id, programme_id, data FROM items{sql_where} ORDER BY {order} LIMIT ? OFFSET ?", [*args, limit, offset]
        ).fetchall()
        return [{"kind": k, "programme_id": p, **unpack(b)} for k, _, p, b in rows], total

    def roles_of(self, items: list[tuple[str, int]], subject_ids: list[int]) -> dict[tuple[str, int], list[str]]:
        out: dict[tuple[str, int], list[str]] = {}
        if not items or not subject_ids:
            return out
        for kind, iid, role in self.db.execute(
            f"SELECT kind, item_id, role FROM parties WHERE subject_id IN ({_marks(subject_ids)})", subject_ids
        ):
            if (kind, iid) in set(items):
                out.setdefault((kind, iid), []).append(role)
        return out

    def search_procurements(
        self, *, buyer_subjects: list[int] | None = None, buyer_suppliers: list[int] | None = None,
        supplier_ids: list[int] | None = None, supplier_subjects: list[int] | None = None,
        text: str | None, project_id: int | None = None, limit: int, offset: int,
    ) -> tuple[list[int], int]:
        """Procurement IDs, newest first, and the number of matches. Organisation filters are ORed."""
        where, args = [], []
        orgs = []
        if buyer_subjects:
            orgs.append(f"buyer_id IN ({_marks(buyer_subjects)})")
            args += buyer_subjects
        if buyer_suppliers:
            orgs.append(f"buyer_supplier_id IN ({_marks(buyer_suppliers)})")
            args += buyer_suppliers
        if supplier_ids:
            orgs.append(f"id IN (SELECT procurement_id FROM contracts WHERE supplier_id IN ({_marks(supplier_ids)}))")
            args += supplier_ids
        if supplier_subjects:
            orgs.append(f"id IN (SELECT procurement_id FROM contracts WHERE supplier_subject_id IN ({_marks(supplier_subjects)}))")
            args += supplier_subjects
        if buyer_subjects is not None or buyer_suppliers is not None or supplier_ids is not None or supplier_subjects is not None:
            where.append("(" + (" OR ".join(orgs) or "0") + ")")
        if text and (q := fts_query(text)):
            where.append("id IN (SELECT rowid FROM procurements_text WHERE procurements_text MATCH ?)")
            args.append(q)
        if project_id is not None:
            where.append("id IN (SELECT procurement_id FROM procurement_projects WHERE project_id = ?)")
            args.append(project_id)
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.execute(f"SELECT count(*) FROM procurements{sql_where}", args).fetchone()[0]
        ids = [i for (i,) in self.db.execute(f"SELECT id FROM procurements{sql_where} ORDER BY id DESC LIMIT ? OFFSET ?", [*args, limit, offset])]
        return ids, total

    def procurement(self, procurement_id: int) -> dict | None:
        """A procurement with its buyer, detail, contracts (with main supplier) and linked projects."""
        row = self.db.execute("SELECT data, detail, detail_fetched FROM procurements WHERE id=?", (procurement_id,)).fetchone()
        if not row:
            return None
        rec, detail = unpack(row[0]), unpack(row[1])
        rec.pop("updated_ms", None)
        out = {"id": rec.pop("id"), "code": rec.pop("code", None), "name": rec.pop("name", None)}
        out["buyer"] = self.short_org(rec.pop("buyer_id", None)) if "buyer_id" in rec else self.short_org(supplier_id=rec.pop("buyer_supplier_id", None))
        rec.pop("buyer_supplier_id", None)
        out.update(rec)
        if detail is None:
            out["note"] = "Details (contracts, suppliers, projects) not loaded yet; run sk-public-money-sync eu."
            return out
        project_ids = detail.pop("project_ids", [])
        out.update(detail)
        contracts = []
        for (blob,) in self.db.execute("SELECT data FROM contracts WHERE procurement_id=? ORDER BY id", (procurement_id,)):
            c = unpack(blob)
            c.pop("procurement_id", None)
            sup = c.pop("supplier_id", None)
            sub = c.pop("supplier_subject_id", None)
            c["main_supplier"] = self.short_org(supplier_id=sup) if sup is not None else self.short_org(sub)
            contracts.append(c)
        out["contracts"] = contracts
        out["projects"] = []
        for pid in project_ids:
            p = self.item("project", pid)
            out["projects"].append({"id": pid, "code": p.get("code"), "name": p.get("name")} if p else {"id": pid})
        return out

    def invoices_by_supplier(self, supplier_ids: list[int], supplier_subjects: list[int], top: int = 10) -> dict | None:
        """Invoices claimed in EU projects where these organisations are the supplier, by recipient."""
        clauses, args = [], []
        if supplier_ids:
            clauses.append(f"supplier_id IN ({_marks(supplier_ids)})")
            args += supplier_ids
        if supplier_subjects:
            clauses.append(f"supplier_subject_id IN ({_marks(supplier_subjects)})")
            args += supplier_subjects
        if not clauses:
            return None
        where = " OR ".join(clauses)
        # Recipients' own internal documents (flat rates, payroll) name the recipient as supplier.
        where = f"({where}) AND (owner_id IS NULL OR supplier_subject_id IS NULL OR owner_id != supplier_subject_id)"
        n, total = self.db.execute(f"SELECT count(*), sum(amount) FROM invoices WHERE {where}", args).fetchone()
        if not n:
            return None
        by_owner = [
            {"recipient": self.short_org(o), "invoices": c, "total": round(s or 0, 2)}
            for o, c, s in self.db.execute(
                f"SELECT owner_id, count(*), sum(amount) s FROM invoices WHERE {where} GROUP BY owner_id ORDER BY s DESC LIMIT ?", [*args, top]
            )
        ]
        return {"count": n, "total": round(total or 0, 2), "by_recipient": by_owner}
