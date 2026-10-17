"""Turn transparent-account records into compact, readable dicts."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote


def money(value: dict | None) -> float | None:
    """{"value": -61500, "precision": 2, "currency": "EUR"} -> -615.0"""
    if not value or value.get("value") is None:
        return None
    return round(value["value"] / 10 ** (value.get("precision") or 0), 2)


def _clean(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return value or None


def account(raw: dict) -> dict:
    row = {
        "name": _clean(raw.get("name")),
        "iban": raw.get("iban"),
        "balance_eur": money(raw.get("balance")),
        "currency": (raw.get("balance") or {}).get("currency"),
        "opened": (raw.get("created") or "")[:10] or None,
        "updated": (raw.get("updated") or "")[:19] or None,
    }
    if row["currency"] == "EUR":
        del row["currency"]
    return {k: v for k, v in row.items() if v is not None}


def turnover(raw: dict) -> dict:
    """One account movement. The running balance is not published (always 0), so it is dropped."""
    row = {
        "date": raw.get("date"),
        "amount_eur": money(raw.get("amount")),
        "currency": (raw.get("amount") or {}).get("currency"),
        "counterparty": _clean(raw.get("counterAccountName")),
        "note": _clean(raw.get("note")),
        "type": _clean(raw.get("description")),
        "vs": _clean(raw.get("variable")),
        "ss": _clean(raw.get("specific")),
        "id": raw.get("turnoverId"),
    }
    if row["currency"] == "EUR":
        del row["currency"]
    return {k: v for k, v in row.items() if v is not None}


# --- annual-report index pages ------------------------------------------------------------------

_LINK = re.compile(r'<a\b[^>]*href="(?P<href>/swift_data/source/statna_komisia_pre_volby/30_annual_report/ar(?P<year>\d{4})/(?P<file>[^"/]+\.pdf))"[^>]*>(?P<text>.*?)</a>(?P<after>[^<]*)', re.S | re.I)
_SIZE = re.compile(r"\s*\((?:PDF|pdf)[^)]*\)\s*$")
_TAGS = re.compile(r"<[^>]+>")
GUIDANCE = re.compile(r"(?i)sprievodn|priloha|n[aá]le[zž]itost|zverej[nň]ovan|metodick|usmernen")


def report_links(html: str, year: int, base: str = "https://www.minv.sk") -> list[dict]:
    """Every party's report on a year's index page: party name as listed, file, URL, whether it is a
    corrected version (file name ends in _f) and whether the commission marked it as deficient (*).
    When a party has both a report and its correction, only the correction is kept."""
    out: dict[str, dict] = {}
    for m in _LINK.finditer(html):
        # Guidance documents sometimes sit next to the reports.
        if int(m["year"]) != year or GUIDANCE.search(m["file"]):
            continue
        text = " ".join(unescape(_TAGS.sub("", m["text"])).split())
        party = _SIZE.sub("", text).strip()
        stem = m["file"][: -len(".pdf")]
        corrected = bool(re.search(r"_f\d*$", stem, re.I))
        after = " ".join(unescape(m["after"]).split())
        row = {
            "year": year,
            "party": party,
            "file": m["file"],
            "url": base + quote(m["href"]),
            "corrected": corrected,
            "deficient": after.startswith("*"),
        }
        note = after.lstrip("*").strip()
        if note:
            row["listing_note"] = note
        key = re.sub(r"_f\d*$", "", stem, flags=re.I).lower()
        if key not in out or corrected:
            out[key] = row
    return list(out.values())


_YEAR_LINK = re.compile(r'<a\b[^>]*href="(?P<href>(?:https?://www\.minv\.sk)?/\?[^"]+)"[^>]*>\s*Výročné správy politických strán a politických hnutí za rok (?P<year>\d{4})\s*</a>', re.I)
_NOT_FILED = re.compile(r"<p>\s*\*\*\s*(?P<text>[^<]*nepredložil[^<]*)</p>", re.I)
_UPDATED = re.compile(r"aktualizácia:\s*(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", re.I)


def year_pages(html: str, base: str = "https://www.minv.sk") -> dict[int, str]:
    """{year: index page URL} from the commission's overview page of annual reports."""
    out = {}
    for m in _YEAR_LINK.finditer(html):
        href = re.sub(r"^https?://www\.minv\.sk", "", unescape(m["href"]))
        out.setdefault(int(m["year"]), base + href)
    return dict(sorted(out.items()))


def year_notes(html: str) -> dict:
    """What a year's index page says besides the links: when it was updated, who filed no report."""
    out = {}
    if m := _UPDATED.search(html):
        out["page_updated"] = f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    if m := _NOT_FILED.search(html):
        out["not_filed"] = " ".join(unescape(m["text"]).split())
    return out
