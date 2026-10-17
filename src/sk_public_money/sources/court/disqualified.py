"""The register of disqualified persons (register diskvalifikácií): people a court or tax office has
banned from statutory bodies of companies (konateľ, člen predstavenstva, prokurista) for a period.

The register is small (about a thousand records), so a local copy is kept in SQLite. The list
endpoint gives name, address, decision date and sometimes the court in two requests; the ban period,
case number and note are only in each record's detail, which is fetched when a record is shown or
matched and kept (the sync fetches all of them). The list is re-read when the copy is a day old, so
checking people from other tools costs no request on most calls.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date

from ... import localdb
from ...http import SourceUnavailable
from ...text import fold
from .client import CourtClient
from .parse import iso_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS disqualified (
    id TEXT PRIMARY KEY,        -- diskvalifikacia_<n>
    name TEXT NOT NULL,
    address TEXT,
    decision_date TEXT,         -- ISO
    court TEXT,
    detail TEXT,                -- the detail record as JSON, once fetched
    detail_at REAL
);
"""
LIST_EVERY = 24 * 3600
DETAIL_EVERY = 30 * 24 * 3600
TITLES = {
    "bc", "mgr", "ing", "judr", "mudr", "mvdr", "mddr", "rndr", "phdr", "paeddr", "thdr", "thlic", "rsdr",
    "dr", "doc", "prof", "phd", "csc", "drsc", "artd", "mba", "mpa", "llm", "msc", "bsc", "ba", "ma", "dis",
    "arch", "art", "akad", "mal", "ingarch", "mgrart",
}


def path():
    return localdb.db_path("court", "disqualified")


def open_db() -> sqlite3.Connection:
    return localdb.connect(path(), SCHEMA)


def name_tokens(name: str | None) -> frozenset[str]:
    """Folded name words without academic titles: 'Ing. Ján Vzorový, PhD.' -> {'jan', 'vzorovy'}."""
    words = re.split(r"[\s,]+", fold(name or ""))
    out = set()
    for w in words:
        bare = w.replace(".", "")
        if bare and bare not in TITLES and not w.endswith("."):
            out.add(re.sub(r"[^\w-]", "", bare))
    return frozenset(w for w in out if w)


def name_match(a: str | None, b: str | None) -> str | None:
    """'exact' when both names have the same words, 'partial' when one adds a word (a second given
    name or surname), else None. Names need at least two words."""
    ta, tb = name_tokens(a), name_tokens(b)
    if len(ta) < 2 or len(tb) < 2:
        return None
    if ta == tb:
        return "exact"
    if ta < tb or tb < ta:
        return "partial"
    return None


def _address_parts(addr: str | None) -> tuple[str | None, str | None, set[str]]:
    """(postal code, folded town, house numbers) of 'Mlynská 1461/79, 09301 Vranov nad Topľou'."""
    psc = town = None
    numbers: set[str] = set()
    for part in (p.strip() for p in (addr or "").split(",")):
        m = re.match(r"^(\d{3})\s?(\d{2})\s+(.+)$", part)
        if m:
            psc, town = m[1] + m[2], " ".join(fold(m[3]).split())
        else:
            numbers |= set(re.findall(r"\d+[a-z]?", fold(part)))
    if town is None and addr:
        town = " ".join(fold(addr.split(",")[-1]).split()) or None
    return psc, town, numbers


def address_match(a: str | None, b: str | None) -> str:
    if not a or not b:
        return "not comparable"
    psc_a, town_a, num_a = _address_parts(a)
    psc_b, town_b, num_b = _address_parts(b)
    same_place = (psc_a and psc_a == psc_b) or (town_a and town_a == town_b)
    if same_place and num_a & num_b:
        return "same address"
    return "same town" if same_place else "different"


def ban_active(ban_from: str | None, ban_to: str | None, today: date | None = None) -> bool | None:
    if not ban_from and not ban_to:
        return None
    day = (today or date.today()).isoformat()
    return (not ban_from or ban_from <= day) and (not ban_to or day <= ban_to)


def record(row: sqlite3.Row, today: date | None = None) -> dict:
    """A stored record with its detail merged in, in the shape the tools return."""
    detail = json.loads(row["detail"]) if row["detail"] else {}
    out = {
        "record_id": row["id"],
        "name": row["name"],
        "address": detail.get("adresa") or row["address"],
        "decision_date": row["decision_date"],
        "court": detail.get("sud") or row["court"],
    }
    if detail.get("ifo"):
        out["ifo"] = detail["ifo"]
    if detail:
        out["case_number"] = detail.get("spisovaZnacka")
        if detail.get("cisloKonania") and detail.get("cisloKonania") != detail.get("spisovaZnacka"):
            out["proceedings_number"] = detail["cisloKonania"]
        if detail.get("poznamka"):
            out["note"] = detail["poznamka"]
        out["ban_from"] = iso_date(detail.get("vylucenieOd"))
        out["ban_to"] = iso_date(detail.get("vylucenieDo"))
        out["ban_active"] = ban_active(out["ban_from"], out["ban_to"], today)
        if out["ban_active"] is None:
            out["ban_period"] = "not given in the record"
        out["updated"] = iso_date(detail.get("updateDate"))
    else:
        out["ban_period"] = "detail could not be fetched; ban period unknown"
        out["ban_active"] = None
    return out


async def refresh_list(db: sqlite3.Connection, client: CourtClient, *, force: bool = False) -> int | None:
    """Re-read the register's list when the copy is a day old (or force). Returns the number of
    records, or None when nothing was fetched."""
    if not force and not localdb.is_due(db, "list", LIST_EVERY):
        return None
    rows = await client.disqualification_list()
    if not rows:
        raise SourceUnavailable("The register of disqualified persons came back empty; keeping the old copy.")
    ids = []
    with db:
        for r in rows:
            ids.append(r["registreGuid"])
            db.execute(
                """INSERT INTO disqualified (id, name, address, decision_date, court) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, address=excluded.address,
                   decision_date=excluded.decision_date, court=excluded.court""",
                (r["registreGuid"], (r.get("meno") or "").strip(), r.get("adresa"), iso_date(r.get("datumRozhodnutia")), r.get("sud")),
            )
        # Records the register no longer lists were removed by it; mirror that.
        db.execute("CREATE TEMP TABLE IF NOT EXISTS listed (id TEXT PRIMARY KEY)")
        db.execute("DELETE FROM listed")
        db.executemany("INSERT OR IGNORE INTO listed VALUES (?)", [(i,) for i in ids])
        db.execute("DELETE FROM disqualified WHERE id NOT IN (SELECT id FROM listed)")
    localdb.mark_done(db, "list")
    return len(ids)


async def fetch_details(db: sqlite3.Connection, client: CourtClient, ids: list[str], *, max_age: float = DETAIL_EVERY) -> None:
    """Fetch the detail (ban period, case number) of these records unless a recent copy is stored."""
    now = time.time()
    for rid in ids:
        row = db.execute("SELECT detail_at FROM disqualified WHERE id = ?", (rid,)).fetchone()
        if row is None or (row["detail_at"] and now - row["detail_at"] < max_age):
            continue
        try:
            detail = await client.api(f"diskvalifikacia/{rid}")
        except LookupError:
            continue  # listed but without detail; shown with the list fields only
        with db:
            db.execute("UPDATE disqualified SET detail = ?, detail_at = ? WHERE id = ?", (json.dumps(detail, ensure_ascii=False), now, rid))


async def ensure_fresh(db: sqlite3.Connection, client: CourtClient) -> str | None:
    """Top the copy up if due. Returns a note when the register could not be reached; raises
    SourceUnavailable only when there is no copy at all."""
    try:
        await refresh_list(db, client)
        return None
    except SourceUnavailable as exc:
        if not db.execute("SELECT 1 FROM disqualified LIMIT 1").fetchone():
            raise SourceUnavailable(f"The register of disqualified persons could not be downloaded: {exc}") from exc
        last = localdb.get_meta(db, "list")
        when = time.strftime("%Y-%m-%d", time.localtime(float(last))) if last else "an earlier day"
        return f"The register could not be reached; using the copy from {when}."


def search(db: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """Records whose name contains every word of name as a word start, ignoring case, diacritics and titles."""
    wanted = [w for w in name_tokens(name)] or [fold(name).strip()]
    out = []
    for row in db.execute("SELECT * FROM disqualified ORDER BY decision_date DESC"):
        words = name_tokens(row["name"])
        if all(any(w.startswith(q) for w in words) for q in wanted):
            out.append(row)
    return out


def _is_person(party: dict) -> bool:
    return bool(party.get("name")) and not party.get("ico")


def is_procurist(party: dict) -> bool:
    return fold(party.get("role") or "").startswith("prokur")


def officers(org: dict) -> list[dict]:
    """The people an RPO organization's check covers: current statutory body members and procurists."""
    return list(org.get("statutory_bodies") or []) + [s for s in org.get("stakeholders") or [] if is_procurist(s)]


async def flag(parties: list[dict], *, client: CourtClient | None = None) -> str | None:
    """Add a 'disqualified' list to each person in parties (dicts with name and address, e.g. RPO
    parties) whose name matches a record of the register; companies (with an ico) are skipped.
    Returns a note when the check could not run or used an old copy, else None.
    """
    from .tools import client as default_client

    people = [p for p in parties if _is_person(p)]
    if not people:
        return None
    client = client or default_client()
    try:
        db = open_db()
    except (sqlite3.Error, OSError) as exc:
        return f"Disqualification check not done: the local copy could not be opened ({exc})."
    try:
        try:
            note = await ensure_fresh(db, client)
        except (SourceUnavailable, sqlite3.Error) as exc:
            return f"Disqualification check not done: {exc}"
        rows = db.execute("SELECT * FROM disqualified").fetchall()
        hits: list[tuple[dict, sqlite3.Row, str]] = []
        for p in people:
            for row in rows:
                how = name_match(p["name"], row["name"])
                if how:
                    hits.append((p, row, how))
        if hits:
            try:
                await fetch_details(db, client, sorted({row["id"] for _, row, _ in hits}))
            except SourceUnavailable:
                pass  # flagged without the ban period; record() says so
            fresh = {r["id"]: r for r in db.execute(
                f"SELECT * FROM disqualified WHERE id IN ({','.join('?' * len(hits))})", [row["id"] for _, row, _ in hits])}
            for p, row, how in hits:
                rec = record(fresh.get(row["id"], row))
                place = address_match(p.get("address"), rec.get("address"))
                matched_by = {"same address": "name and address", "same town": "name and town"}.get(place, "name")
                flag_ = {
                    "record_id": rec["record_id"],
                    "matched_by": matched_by,
                    "name_match": how,
                    "register_name": rec["name"],
                    "register_address": rec.get("address"),
                    "address_comparison": place,
                    **{k: rec[k] for k in ("ban_from", "ban_to", "ban_period", "ban_active", "decision_date", "court") if k in rec},
                }
                p.setdefault("disqualified", []).append(flag_)
        return note
    finally:
        db.close()
