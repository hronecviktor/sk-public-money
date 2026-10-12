"""Turn RÚZ API records into compact, readable dicts, and read key figures out of report tables.

A report (účtovný výkaz) stores each table as one flat list of strings, read row by row, left to right.
Its template (šablóna) holds the row labels and a header of possibly merged cells. Templates differ in
how many value columns they have and in which order: a balance sheet of a company has Brutto,
Korekcia, Netto for the current year and Netto for the previous one, a sole trader's lists the previous
year first, and a non-profit's income table splits the current year into taxed and untaxed activity
without a total. So the current-year column is chosen from the header texts, never by position, and
rows are found by their labels. A figure that can't be found this way is reported as not mapped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...text import fold
from .client import BASE_URL

DELETED = "ZMAZANÉ"
ACCESS = {"Verejné": "public", "Verejné prílohy": "attachments only", "Neverejné": "not public"}


def _clean(value) -> str | None:
    value = (str(value) if value is not None else "").strip()
    return value or None


def ico(value: str) -> str:
    """Normalize an IČO: drop spaces, zero-pad Slovak IČOs to 8 digits."""
    value = re.sub(r"\s+", "", value or "")
    if not value.isdigit() or len(value) > 8:
        raise ValueError(f"An IČO has up to 8 digits, got: {value!r}")
    return value.zfill(8)


def entity_url(entity_id: int) -> str:
    return f"{BASE_URL}/domain/accountingentity/show/{entity_id}"


def attachment_url(attachment_id: int) -> str:
    return f"{BASE_URL}/domain/financialreport/attachment/{attachment_id}"


def report_pdf_url(report_id: int) -> str:
    return f"{BASE_URL}/domain/financialreport/pdf/{report_id}"


def entity(rec: dict, codebooks: dict[str, dict[str, str]]) -> dict:
    """One účtovná jednotka. codebooks maps code-list names to {code: name}."""

    def named(field: str, book: str) -> str | None:
        code = rec.get(field)
        return codebooks.get(book, {}).get(code, code) if code else None

    street, town = _clean(rec.get("ulica")), " ".join(p for p in (_clean(rec.get("psc")), _clean(rec.get("mesto"))) if p)
    out = {
        "ruz_id": rec["id"],
        "url": entity_url(rec["id"]),
        "name": _clean(rec.get("nazovUJ")),
        "ico": _clean(rec.get("ico")),
        "dic": _clean(rec.get("dic")),
        "address": ", ".join(p for p in (street, town) if p) or None,
        "legal_form": named("pravnaForma", "pravne-formy"),
        "sk_nace": f"{rec['skNace']} {codebooks.get('sk-nace', {}).get(rec['skNace'], '')}".strip() if rec.get("skNace") else None,
        # ŠÚSR's current size band; the statements themselves carry no employee count.
        "employees": named("velkostOrganizacie", "velkosti-organizacie"),
        "ownership": named("druhVlastnictva", "druhy-vlastnictva"),
        "founded": rec.get("datumZalozenia"),
        "dissolved": rec.get("datumZrusenia"),
        "status": rec.get("stav"),
        "data_source": rec.get("zdrojDat"),
    }
    return {k: v for k, v in out.items() if v is not None}


def statement_kind(rec: dict) -> str:
    if rec.get("konsolidovana"):
        return "consolidated"
    if rec.get("konsolidovanaZavierkaUstrednejStatnejSpravy"):
        return "consolidated (central government)"
    if rec.get("suhrnnaUctovnaZavierkaVerejnejSpravy"):
        return "summary (public administration)"
    return "individual"


def period_end(rec: dict) -> str:
    return rec.get("obdobieDo") or rec.get("datumZostaveniaK") or ""


def statement(rec: dict) -> dict:
    """One účtovná závierka, without its reports."""
    end = period_end(rec)
    out = {
        "statement_id": rec["id"],
        "year": int(end[:4]) if end[:4].isdigit() else None,
        "period": f"{rec.get('obdobieOd', '?')} to {rec.get('obdobieDo', '?')}",
        "kind": statement_kind(rec),
        "type": rec.get("typ"),
        "fund": rec.get("nazovFondu"),
        "filed": rec.get("datumPodania"),
        "approved": rec.get("datumSchvalenia"),
        "auditor_report_attached": rec.get("datumPrilozeniaSpravyAuditora"),
        "reports": len(rec.get("idUctovnychVykazov", [])),
    }
    return {k: v for k, v in out.items() if v is not None}


def document(report: dict, template: dict | None) -> dict:
    """A report as a document: its template name, availability and downloadable files."""
    out = {
        "report_id": report["id"],
        "template": (template or {}).get("nazov") or f"template {report.get('idSablony')}",
        "access": ACCESS.get(report.get("pristupnostDat"), report.get("pristupnostDat")),
    }
    if report.get("obsah", {}).get("tabulky"):
        out["pdf"] = report_pdf_url(report["id"])
    files = [
        {k: v for k, v in (("name", a.get("meno")), ("url", attachment_url(a["id"])), ("pages", a.get("pocetStran") or None)) if v}
        for a in report.get("prilohy", [])
    ]
    if files:
        out["attachments"] = files
    return out


# --- key figures -------------------------------------------------------------------------------

def _text(cell: dict) -> str:
    return " ".join(((cell.get("text") or {}).get("sk") or "").split())


def _number(value: str):
    value = (value or "").strip()
    if not value:
        return None
    number = float(value)
    return int(number) if number.is_integer() else round(number, 2)


@dataclass
class Table:
    """One report table joined with its template table."""

    name: str
    rows: list[dict]  # template rows: cisloRiadku, oznacenie, text
    values: list[list[str]]  # values[row][column]
    columns: list[str]  # header path of each value column

    def current_column(self) -> tuple[list[int], str] | str:
        """Indexes of the current-period value column(s) and a label, or the reason none was found."""
        current = [i for i, path in enumerate(self.columns) if not _is_previous(path)]
        if not current:
            return f"no current-period column in table {self.name!r} (columns: {self.columns})"
        if len(current) > 1:
            for word in ("netto", "spolu"):
                chosen = [i for i in current if word in fold(self.columns[i])]
                if len(chosen) == 1:
                    return chosen, self.columns[chosen[0]]
            # Split by activity (taxed/untaxed) with no total column: the current year is their sum.
            return current, "sum of " + " + ".join(self.columns[i] for i in current)
        return current, self.columns[current[0]]


def _is_previous(path: str) -> bool:
    path = fold(path)
    return "predchadzaj" in path or re.search(r"20xx\s*-\s*1", path) is not None


def _header_paths(header: list[dict], count: int) -> list[str]:
    """Header text over each of the last `count` columns, top to bottom; merged cells count for each column they span."""
    last = max((h.get("stlpec", 0) + h.get("sirkaStlpca", 1) - 1 for h in header), default=0)
    paths = []
    for col in range(last - count + 1, last + 1):
        cells = sorted(
            (h for h in header if h.get("stlpec", 0) <= col < h.get("stlpec", 0) + h.get("sirkaStlpca", 1)),
            key=lambda h: h.get("riadok", 0),
        )
        # Skip column numbering ("1", "a") but keep labels like "Netto 2".
        texts = [t for t in (_text(h) for h in cells) if t and not re.fullmatch(r"\w?|\d+", t)]
        paths.append(" / ".join(texts) or f"column {col}")
    return paths


def tables(report: dict, template: dict) -> tuple[list[Table], list[str]]:
    """Join a report's data with its template. Returns (tables, problems)."""
    data_tables = report.get("obsah", {}).get("tabulky", [])
    template_tables = template.get("tabulky", [])
    out, problems = [], []
    for i, data in enumerate(data_tables):
        name = (data.get("nazov") or {}).get("sk") or f"table {i + 1}"
        tpl = template_tables[i] if i < len(template_tables) else None
        if tpl is None or fold((tpl.get("nazov") or {}).get("sk", "")) != fold(name):
            problems.append(f"table {name!r} does not match template {template.get('id')}")
            continue
        rows, values = tpl.get("riadky", []), data.get("data", [])
        if not rows or len(values) % len(rows):
            problems.append(f"table {name!r} has {len(values)} values for {len(rows)} rows")
            continue
        width = len(values) // len(rows)
        out.append(Table(
            name=name,
            rows=rows,
            values=[values[r * width:(r + 1) * width] for r in range(len(rows))],
            columns=_header_paths(tpl.get("hlavicka", []), width),
        ))
    return out, problems


@dataclass(frozen=True)
class Rule:
    tables: str  # regex searched in the folded table name
    rows: tuple[str, ...]  # regexes matched at the start of folded row labels
    sum: bool = False  # add up every matching row instead of taking the first
    require_all: bool = False  # with sum: each regex must match some row
    note: str | None = None


PL = r"ziskov|vynos|naklad|vysledovka|prijm|vydav"
FIGURES: dict[str, tuple[Rule, ...]] = {
    "revenue": (
        # Public administration: one aggregate row over its 601/602/604 sub-rows.
        Rule(PL, (r"trzby za vlastne vykony a tovar",)),
        Rule(PL, (r"trzby z predaja (tovaru|vlastnych vyrobkov|sluzieb)", r"trzby za (vlastne vyrobky|predany tovar)"), sum=True),
        Rule(r"prijm", (r"predaj (tovaru|vyrobkov a sluzieb)", r"z poskytovania sluzieb a predaja vlastnych vyrobkov"), sum=True,
             note="sales income of single-entry bookkeeping (cash received)"),
    ),
    "total_income": (
        Rule(PL, (r"uctova trieda 6 (spolu|sucet)",)),
        Rule(PL, (r"vynosy z hospodarskej cinnosti spolu", r"vynosy z financnej cinnosti spolu"), sum=True, require_all=True),
        Rule(r"prijm", (r"prijmy celkom",), note="cash income of single-entry bookkeeping"),
    ),
    "net_profit": (
        Rule(PL, (r"vysledok hospodarenia za uctovne obdobie po zdaneni", r"vysledok hospodarenia po zdaneni")),
        Rule(r"vydav|prijm", (r"rozdiel prijmov a vydavkov",), note="income minus expenses of single-entry bookkeeping"),
    ),
    "total_assets": (
        Rule(r"aktiv|majetok", (r"(spolu majetok|majetok spolu|majetok celkom)",)),
    ),
    "equity": (
        Rule(r"pasiv|zavazk", (r"vlastne imanie(?! a zavazky)\b", r"vlastne zdroje krytia majetku spolu")),
        Rule(r"zavazk", (r"rozdiel majetku a zavazkov",), note="assets minus liabilities; single-entry bookkeeping has no equity"),
    ),
}


def _row_label(table: Table, r: int) -> str:
    row = table.rows[r]
    return f"{table.name}, r. {row.get('cisloRiadku', r + 1)}: {_text(row)}"


def _apply(rule: Rule, all_tables: list[Table]) -> dict | str | None:
    """A figure from the first table and rows that rule matches; a reason string when matched rows can't be read."""
    for table in all_tables:
        if not re.search(rule.tables, fold(table.name)):
            continue
        labels = [fold(_text(row)) for row in table.rows]
        hits = [r for r, label in enumerate(labels) if any(re.match(p, label) for p in rule.rows)]
        if not hits:
            continue
        if rule.require_all and not all(any(re.match(p, labels[r]) for r in hits) for p in rule.rows):
            continue
        if not rule.sum:
            hits = hits[:1]
        column = table.current_column()
        if isinstance(column, str):
            return column
        indexes, column_label = column
        cells = [_number(table.values[r][i]) for r in hits for i in indexes]
        filled = [c for c in cells if c is not None]
        out = {
            "value": round(sum(filled), 2) if filled else None,
            "rows": [_row_label(table, r) for r in hits],
            "column": column_label,
        }
        if not filled:
            out["blank"] = "the report leaves these cells empty"
        if rule.note:
            out["note"] = rule.note
        return out
    return None


def key_figures(reports: list[tuple[dict, dict]]) -> tuple[dict, dict]:
    """Key figures of one statement from its (report, template) pairs.

    Returns (figures, not_mapped): figures[name] = {value, rows, column[, note]}, and a reason for
    each figure that could not be read.
    """
    all_tables, problems = [], []
    for report, template in reports:
        found, issues = tables(report, template)
        all_tables += found
        problems += issues
    figures, not_mapped = {}, {}
    for name, rules in FIGURES.items():
        reason = None
        for rule in rules:
            result = _apply(rule, all_tables)
            if isinstance(result, dict):
                figures[name] = result
                break
            reason = reason or result
        else:
            if not all_tables:
                reason = "no report of this statement has structured data"
            not_mapped[name] = reason or "no row with a known label in " + (
                ", ".join(sorted({t.name for t in all_tables})) or "the reports"
            )
    if problems:
        not_mapped["_problems"] = "; ".join(problems)
    return figures, not_mapped
