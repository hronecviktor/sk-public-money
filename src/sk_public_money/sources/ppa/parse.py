"""Reading PPA's lists of CAP beneficiaries (Annex VIII of Implementing Regulation (EU) 2022/128).

Each financial year (16 October to 15 October) is one XLSX sheet: a few title rows, a header row,
then per beneficiary its operations (one row per measure code, specific objective and payment) and a
subtotal row that has no measure code. The two years published so far differ: FY2024 has start and
end date columns (always empty) and repeats the name and obec on the subtotal row; FY2025 joins a
person's first name and surname into the subtotal's name column. So columns are found by their
header, and operations belong to the beneficiary whose subtotal row follows them.

Recipients of EUR 1,250 or less a year are published as a numeric code, and where a person could be
identified from a small obec, the okres (district) is given instead ("Okres Komárno" in FY2024).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from ...text import fold

# Header prefixes (folded) -> field. Matching by prefix copes with trailing spaces and line breaks.
COLUMNS = {
    "meno prijimatela": "name",
    "priezvisko prijimatela": "surname",
    "ak patri k skupine": "parent",
    "obec": "place",
    "kod opatrenia": "code",
    "specificky ciel": "objective",
    "suma za operaciu v ramci epzf": "epzf",
    "suma za operaciu v ramci epfrv": "epfrv",
    "suma za operaciu v ramci spolufinancovania": "cofinancing",
    "celkova suma z prostriedkov eu a spolufinancovana suma": "subtotal",
}
REQUIRED = ("name", "surname", "place", "code", "epzf", "epfrv", "cofinancing")
EMPTY = {"", "(prázdne)", "(prazdne)"}
ANONYMISED_SURNAME = "anonymizované"
FUNDS = ("epzf", "epfrv", "cofinancing")

# Annex IX of Implementing Regulation (EU) 2022/128 (version of 16 May 2024).
MEASURES = {
    "I.1": "Basic income support for sustainability",
    "I.2": "Complementary redistributive income support for sustainability",
    "I.3": "Complementary income support for young farmers",
    "I.4": "Eco-schemes (schemes for the climate, the environment and animal welfare)",
    "I.5": "Small farmers' payments",
    "I.6": "Coupled income support",
    "I.7": "Crop-specific payment for cotton",
    "II.1": "Basic payment scheme (Reg. 1307/2013)",
    "II.2": "Single area payment scheme (Reg. 1307/2013)",
    "II.3": "Redistributive payment (Reg. 1307/2013)",
    "II.4": "Greening: practices beneficial for the climate and the environment (Reg. 1307/2013)",
    "II.5": "Payment for areas with natural constraints (Reg. 1307/2013)",
    "II.6": "Payment for young farmers (Reg. 1307/2013)",
    "II.7": "Voluntary coupled support (Reg. 1307/2013)",
    "II.8": "Crop-specific payment for cotton (Reg. 1307/2013)",
    "II.9": "Small farmers scheme (Reg. 1307/2013)",
    "II.10": "Direct payments under Annex I to Reg. 73/2009",
    "III.1": "Sectoral intervention: fruit and vegetables",
    "III.2": "Sectoral intervention: apiculture",
    "III.3": "Sectoral intervention: wine",
    "III.4": "Sectoral intervention: hops",
    "III.5": "Sectoral intervention: olive oil and table olives",
    "III.6": "Sectoral intervention: other sectors",
    "IV.1": "Public intervention (market measure)",
    "IV.2": "Aid for private storage",
    "IV.3": "School fruit, vegetables and milk scheme",
    "IV.5": "Exceptional market measures",
    "IV.6": "Aid in the fruit and vegetables sector (Reg. 1308/2013)",
    "IV.7": "Support in the wine sector (Reg. 1308/2013)",
    "IV.8": "Support in the olive oil and table olives sector (Reg. 1308/2013)",
    "IV.9": "Aid in the apiculture sector (Reg. 1308/2013)",
    "IV.10": "Aid in the hops sector (Reg. 1308/2013)",
    "V.1": "Rural development: environmental, climate and other management commitments",
    "V.2": "Rural development: natural or other area-specific constraints",
    "V.3": "Rural development: area-specific disadvantages from mandatory requirements (Natura 2000, water)",
    "V.4": "Rural development: investments, including irrigation",
    "V.5": "Rural development: setting-up of young farmers, new farmers and rural business start-up",
    "V.6": "Rural development: risk management tools",
    "V.7": "Rural development: cooperation (incl. LEADER)",
    "V.8": "Rural development: knowledge exchange and information",
    "VI.1": "RDP 2014-2022: knowledge transfer and information actions",
    "VI.2": "RDP 2014-2022: advisory, farm management and farm relief services",
    "VI.3": "RDP 2014-2022: quality schemes for agricultural products and foodstuffs",
    "VI.4": "RDP 2014-2022: investments in physical assets",
    "VI.5": "RDP 2014-2022: restoring production potential after natural disasters, prevention",
    "VI.6": "RDP 2014-2022: farm and business development",
    "VI.7": "RDP 2014-2022: basic services and village renewal in rural areas",
    "VI.8": "RDP 2014-2022: investments in forest area development and viability of forests",
    "VI.9": "RDP 2014-2022: afforestation and creation of woodland",
    "VI.10": "RDP 2014-2022: agroforestry systems",
    "VI.11": "RDP 2014-2022: prevention and restoration of damage to forests",
    "VI.12": "RDP 2014-2022: resilience and environmental value of forest ecosystems",
    "VI.13": "RDP 2014-2022: forest technologies, processing and marketing of forest products",
    "VI.14": "RDP 2014-2022: setting up of producer groups and organisations",
    "VI.15": "RDP 2014-2022: agri-environment-climate",
    "VI.16": "RDP 2014-2022: organic farming",
    "VI.17": "RDP 2014-2022: Natura 2000 and Water Framework Directive payments",
    "VI.18": "RDP 2014-2022: areas facing natural or other specific constraints",
    "VI.19": "RDP 2014-2022: animal welfare",
    "VI.20": "RDP 2014-2022: forest-environmental and climate services, forest conservation",
    "VI.21": "RDP 2014-2022: co-operation",
    "VI.22": "RDP 2014-2022: risk management",
    "VI.24": "RDP 2014-2022: LEADER local development",
    "VI.25": "RDP 2014-2022: technical assistance",
}
# Specific objectives of the CAP, Article 6 of Regulation (EU) 2021/2115.
OBJECTIVES = {
    "SO1": "viable farm income and resilience",
    "SO2": "market orientation and competitiveness",
    "SO3": "farmers' position in the value chain",
    "SO4": "climate change mitigation and adaptation",
    "SO5": "efficient management of natural resources",
    "SO6": "biodiversity, habitats and landscapes",
    "SO7": "young and new farmers, business development in rural areas",
    "SO8": "employment, growth and local development in rural areas",
    "SO9": "food, health and animal welfare",
    "XCO": "cross-cutting: knowledge, innovation and digitalisation",
}
FISCAL_YEAR = re.compile(r"finančný rok\s+(\d{4})", re.IGNORECASE)


class FormatError(ValueError):
    """The workbook is not laid out as expected (PPA changed the format)."""


def text(value) -> str | None:
    """A cell as clean text: whitespace collapsed, '(prázdne)' and empty cells as None."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    out = " ".join(str(value).split())
    return None if out.lower() in EMPTY else out


def amount(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = (text(value) or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError as exc:
        raise FormatError(f"not an amount: {value!r}") from exc


def measure_code(value) -> str | None:
    """' VI.4', 'II.2.' -> 'VI.4', 'II.2'."""
    code = text(value)
    return code.rstrip(".") if code else None


def objectives(value) -> list[str]:
    """'SO1,SO2' or 'S04' -> ['SO1', 'SO2'] / ['SO4']."""
    out = []
    for part in (text(value) or "").split(","):
        part = re.sub(r"^S0", "SO", part.strip().upper())
        if part and part not in out:
            out.append(part)
    return out


def place_key(place: str | None) -> str:
    """Folded obec without 'Okres ': the published place of an anonymised recipient is its okres."""
    key = fold(place or "").strip()
    return key.removeprefix("okres ")


def parse_parent(value) -> dict | None:
    """'Firma s.r.o. / SK2020000000 / 2020000000' -> name and its VAT and tax numbers as published."""
    raw = text(value)
    if not raw:
        return None
    # Split on slashes between spaces only: foreign tax numbers such as 156/116/00124 have slashes.
    name, *ids = [p.strip() for p in re.split(r"(?:^|\s)/(?=\s|$)", raw)]
    return {"name": name, "ids": [i for i in ids if i]}


def _header(row: tuple) -> dict[str, int] | None:
    cells = [fold(" ".join(str(c).split())) if c is not None else "" for c in row]
    if not cells or not cells[0].startswith("meno prijimatela"):
        return None
    found = {}
    for i, cell in enumerate(cells):
        for prefix, field in COLUMNS.items():
            if cell.startswith(prefix) and field not in found:
                found[field] = i
    missing = [f for f in REQUIRED if f not in found]
    if missing:
        raise FormatError(f"header row lacks the columns {missing}: {row}")
    return found


def _identity(name: str | None, surname: str | None) -> tuple[str, str]:
    """(display name, kind) of a row: an anonymised code, a natural person or an organisation."""
    name = name or ""
    if surname == ANONYMISED_SURNAME or (name.isdigit() and not surname):
        return name, "anonymised"
    if surname:
        return f"{name} {surname}".strip(), "natural_person"
    return name, "entity"


def _beneficiary(ops: list[dict], subtotal_row: dict | None, seq: int) -> dict:
    names: dict[str, int] = {}
    for op in ops:
        names[op["name"]] = names.get(op["name"], 0) + 1
    kind = ops[0]["kind"]
    shown = max(names, key=names.get)
    if subtotal_row is not None and kind != "anonymised":
        # The subtotal row names the beneficiary once, where operations sometimes spell it differently.
        sub_name, _ = _identity(text(subtotal_row.get("name")), text(subtotal_row.get("surname")))
        same = [n for n in names if fold(n) == fold(sub_name)]
        shown = same[0] if same else (sub_name or shown)
    places: dict[str, int] = {}
    for op in ops:
        if op["place"]:
            places[op["place"]] = places.get(op["place"], 0) + 1
    parents = [op["parent"] for op in ops if op["parent"]]
    amounts: dict[tuple, dict] = {}
    for op in ops:
        key = (op["code"], ", ".join(op["objectives"]), op["place"])
        row = amounts.setdefault(key, {"code": op["code"], "objectives": op["objectives"], "place": op["place"],
                                       "epzf": 0.0, "epfrv": 0.0, "cofinancing": 0.0, "operations": 0})
        for f in FUNDS:
            row[f] += op[f]
        row["operations"] += 1
    rows = list(amounts.values())
    for row in rows:
        for f in FUNDS:
            row[f] = round(row[f], 2)
        row["total"] = round(sum(row[f] for f in FUNDS), 2)
    out = {
        "seq": seq,
        "name": shown,
        "kind": kind,
        "also_named": sorted(n for n in names if n != shown),
        "places": sorted(places, key=lambda p: -places[p]),
        "parent": parents[0] if parents else None,
        "amounts": rows,
        "operations": len(ops),
    }
    for f in FUNDS:
        out[f] = round(sum(r[f] for r in rows), 2)
    out["total"] = round(sum(out[f] for f in FUNDS), 2)
    if subtotal_row is not None and subtotal_row.get("subtotal") not in (None, ""):
        published = round(amount(subtotal_row["subtotal"]), 2)
        if abs(published - out["total"]) > 0.05:
            out["published_total"] = published
    return out


def read(path: Path) -> tuple[dict, Iterator[dict]]:
    """Open a workbook: ({fiscal_year, sheet}, an iterator of beneficiaries). Streams the rows."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    meta: dict = {"sheet": ws.title}
    columns = None
    for row in rows:
        first = text(row[0]) if row else None
        if first and (m := FISCAL_YEAR.search(first)):
            meta["fiscal_year"] = int(m.group(1))
        if (columns := _header(row)) is not None:
            break
    if columns is None:
        wb.close()
        raise FormatError(f"{path.name}: no header row starting 'Meno prijímateľa'")
    if "fiscal_year" not in meta:
        wb.close()
        raise FormatError(f"{path.name}: no title row naming the 'finančný rok'")
    return meta, _beneficiaries(wb, rows, columns)


def _beneficiaries(wb, rows, columns: dict[str, int]) -> Iterator[dict]:
    ops: list[dict] = []
    seq = 0
    try:
        for row in rows:
            cells = {f: (row[i] if i < len(row) else None) for f, i in columns.items()}
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
                continue
            if cells["code"] is None or (isinstance(cells["code"], str) and not cells["code"].strip()):
                # A subtotal row closes the beneficiary whose operations precede it.
                if ops:
                    seq += 1
                    yield _beneficiary(ops, cells, seq)
                    ops = []
                continue
            name, surname = text(cells["name"]), text(cells["surname"])
            shown, kind = _identity(name, surname)
            ops.append({
                "name": shown, "kind": kind,
                "parent": parse_parent(cells.get("parent")),
                "place": text(cells["place"]),
                "code": measure_code(cells["code"]),
                "objectives": objectives(cells.get("objective")),
                **{f: amount(cells[f]) for f in FUNDS},
            })
        if ops:
            seq += 1
            yield _beneficiary(ops, None, seq)
    finally:
        wb.close()
