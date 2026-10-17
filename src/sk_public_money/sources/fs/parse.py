"""Reading the Finančná správa list files: zipped XML, one <ITEM> per record.

Each zip holds <name>.xml (and its XSD). The XML starts with the list's date
(<DatumAktualizacieZoznamu>DDMMYYYY</...>) and then has one <ITEM> per record whose child elements
are the fields; empty fields are left out. Recent files are UTF-8, archive files up to 2023
Windows-1250 (declared in the XML header, so lxml decodes both). The files are large (up to 300 MB
of XML), so they are read as a stream.

The published values are messy: names padded with spaces and stray quotes (" CCL s.r.o. "), dates
as DD.MM.YYYY or DDMMYYYY, some IČOs 12 digits long (organisational units: IČO + 4 digits), a
handful of nonsense years of violation (0, 25, 525). Values are cleaned only where that is safe;
anything that does not parse is passed on as published.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from lxml import etree

from ...text import fold

DATE_DOTS = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
DATE_DIGITS = re.compile(r"(\d{2})(\d{2})(\d{4})")
SPACES = re.compile(r"\s+")
QUOTES = "\"„“”'"


class ListFile:
    """Iterate the records of a list zip as {FIELD: text}; .as_of is the list's date (ISO) once
    iteration has started."""

    def __init__(self, path: Path):
        self.path = path
        self.as_of: str | None = None

    def __iter__(self) -> Iterator[dict[str, str]]:
        with zipfile.ZipFile(self.path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".xml")]
            if not names:
                raise ValueError(f"{self.path.name} holds no XML file")
            with z.open(names[0]) as fh:
                for _, el in etree.iterparse(fh, tag=("DatumAktualizacieZoznamu", "ITEM"), huge_tree=True):
                    if el.tag == "ITEM":
                        yield {child.tag: child.text for child in el if child.text is not None}
                        # Free what has been read: the files are too large to keep as a tree.
                        el.clear()
                        while el.getprevious() is not None:
                            del el.getparent()[0]
                    else:
                        self.as_of = parse_date(el.text)


def clean(value: str | None) -> str | None:
    """Collapse whitespace; None for an empty value."""
    if value is None:
        return None
    value = SPACES.sub(" ", value).strip()
    return value or None


def clean_name(value: str | None) -> str | None:
    """A name without the padding inside its quotes: '„ A. Kmeťa 8 "' -> '„A. Kmeťa 8"'. The quotes
    themselves are kept: they are part of many registered names."""
    value = clean(value)
    if not value:
        return value
    if value[0] in QUOTES:
        value = value[0] + value[1:].lstrip()
    if value[-1] in QUOTES:
        value = value[:-1].rstrip() + value[-1]
    return value


def name_key(value: str | None) -> str:
    """A name for exact comparison across lists: folded, letters and digits only."""
    return re.sub(r"[^0-9a-z]+", "", fold(value or ""))


def parse_date(value: str | None) -> str | None:
    """'07.10.2026' or '07102026' -> '2026-10-07'; anything else as published (None when empty)."""
    value = clean(value)
    if not value:
        return None
    m = DATE_DOTS.fullmatch(value) or DATE_DIGITS.fullmatch(value)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass
    return value


def cents(value: str | None) -> int | None:
    """'1522.86' -> 152286."""
    value = clean(value)
    if value is None:
        return None
    try:
        return int((Decimal(value.replace(",", ".")) * 100).to_integral_value())
    except InvalidOperation:
        return None


def eur(value: int | None) -> float | None:
    return None if value is None else value / 100


def ico(value: str | None) -> str | None:
    """IČO as text: digits only, Slovak IČOs zero-padded to 8; the 12-digit IČOs of organisational
    units are kept whole."""
    digits = re.sub(r"\D", "", value or "")
    if not digits or not int(digits):
        return None
    return digits.zfill(8) if len(digits) <= 8 else digits


def dic_of(ic_dph: str | None) -> str | None:
    """The DIČ inside a Slovak IČ DPH (SK + DIČ); None for foreign numbers."""
    m = re.fullmatch(r"SK(\d{10})", (ic_dph or "").replace(" ", "").upper())
    return m.group(1) if m else None


def vat_period(value: str | None) -> str | None:
    """A VAT return period: '0326' -> '2026-03' (month), 'Q126' -> '2026-Q1' (quarter); anything
    else as published."""
    value = clean(value)
    if not value:
        return None
    m = re.fullmatch(r"(0[1-9]|1[0-2])(\d{2})", value)
    if m:
        return f"20{m.group(2)}-{m.group(1)}"
    m = re.fullmatch(r"Q([1-4])(\d{2})", value)
    if m:
        return f"20{m.group(2)}-Q{m.group(1)}"
    return value


def violation_year(value: str | None) -> int | str | None:
    """The year a VAT payer broke its duties; a value that is no plausible year is passed on as text."""
    value = clean(value)
    if not value:
        return None
    if re.fullmatch(r"(19|20)\d\d", value):
        return int(value)
    return value


def place(street: str | None, psc: str | None, town: str | None, country: str | None = None) -> str | None:
    """'Lúčna 825/10, 09301 Vranov nad Topľou' (+ ', country' outside Slovakia)."""
    street, psc, town, country = clean(street), clean(psc), clean(town), clean(country)
    if psc in (".", "0"):
        psc = None
    parts = [street, " ".join(p for p in (psc, town) if p)]
    if country and country not in ("SK", "Slovensko"):
        parts.append(country)
    return ", ".join(p for p in parts if p) or None


# --- one row mapper per list: published fields -> the columns the index stores ------------------


def subject(item: dict, name_field: str) -> tuple | None:
    """(dic, ico, name, place) from a list that carries a DIČ (or a Slovak IČ DPH)."""
    dic = clean(item.get("DIC")) or dic_of(item.get("IC_DPH"))
    if not dic:
        return None
    address = place(item.get("ULICA_CISLO"), item.get("PSC"), item.get("OBEC"), item.get("STAT"))
    return dic, ico(item.get("ICO")), clean_name(item.get(name_field)), address


def ids_row(item: dict) -> tuple | None:
    dic = clean(item.get("DIC"))
    rating = clean(item.get("IDS"))
    return (dic, rating) if dic and rating else None


def vat_row(item: dict) -> tuple | None:
    """ds_dphs: (ic_dph, ico, kind, registered, valid_from, kind_changed)."""
    ic_dph = clean(item.get("IC_DPH"))
    if not ic_dph:
        return None  # a few records carry only a registration kind and dates, no taxpayer
    return (
        ic_dph, ico(item.get("ICO")), clean(item.get("DRUH_REG_DPH")),
        parse_date(item.get("DATUM_REG")), parse_date(item.get("PLAT_DPH_OD")), parse_date(item.get("DATUM_ZMENY_DRUHU_REG")),
    )


def flag_row(item: dict, list_name: str) -> tuple:
    """ds_dphz / ds_dphv: (list, ic_dph, ico, name, place, town, year, published, struck_off)."""
    town = clean(item.get("OBEC"))
    year = violation_year(item.get("ROK_PORUSENIA"))
    return (
        list_name, clean(item.get("IC_DPH")), ico(item.get("ICO")), clean_name(item.get("NAZOV")),
        place(item.get("ADRESA"), item.get("PSC"), town), town, None if year is None else str(year),
        parse_date(item.get("DAT_ZVEREJNENIA")), parse_date(item.get("DAT_VYMAZU")),
    )


def cit_row(item: dict, release: str) -> tuple | None:
    """ds_dppos: (dic, period_from, period_to, tax, additional_tax, loss, release); amounts in cents."""
    dic = clean(item.get("DIC"))
    if not dic:
        return None
    return (
        dic, parse_date(item.get("ZDAN_OBDOBIE_OD")), parse_date(item.get("ZDAN_OBDOBIE_DO")),
        cents(item.get("VYRUBENA_DAN")), cents(item.get("DODAT_VYRUBENA_DAN")), cents(item.get("DANOVA_STRATA")), release,
    )


def vat_return_row(item: dict) -> tuple | None:
    """ds_dphno: (dic, period, excess_deduction, own_liability); amounts in cents."""
    dic = clean(item.get("DIC")) or dic_of(item.get("IC_DPH"))
    period = vat_period(item.get("ZDAN_OBDOBIE"))
    if not dic or not period:
        return None
    return dic, period, cents(item.get("NADMERNY_ODPOCET")), cents(item.get("VLAST_DAN_POV"))


def debtor_row(item: dict) -> tuple | None:
    """ds_dsdd: (name, name_key, amount, place, town)."""
    name = clean_name(item.get("NAZOV_SUBJEKTU"))
    if not name:
        return None
    town = clean(item.get("OBEC"))
    return name, name_key(name), cents(item.get("CIASTKA")), place(item.get("ULICA_CISLO"), item.get("PSC"), town), town
