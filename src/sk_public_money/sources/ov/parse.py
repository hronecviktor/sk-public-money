"""Parse Obchodný vestník notices (the XML forms of the Ministry of Justice's portal, as kept in
Slovensko.Digital's Datahub raw_issues) into compact records keyed by the companies' IČO.

Every notice is one XML file; its kind is in the file name (L006019-OV_LIKVIDATOR_2020-v-1.xml).
Forms differ by kind, so extraction is generic: the subject companies are the Ico/ICO elements that
are not inside a role container (trustee, petitioner, liquidator, partner...), the title is the
form's own label of the notice type, the text its free-text parts, and the remaining fields are
kept as short "Field: value" details. Only kinds useful for following a company are kept.
"""

from __future__ import annotations

import re

from lxml import etree

from ..insolvency.parse import html_text

# Kind (from the file name) -> category, by the first pattern that matches. Unlisted kinds are skipped.
CATEGORIES = [
    ("register", re.compile(r"^OV_OR_PODANIE")),
    ("liquidation", re.compile(r"LIKVID")),
    ("insolvency", re.compile(r"KONKURZ|RESTRUKT|VYROVNAN|SPRAVCOV")),
    ("capital", re.compile(r"ZNIZEN|IMANI|KAPITAL")),
    ("dissolution", re.compile(r"ZLU|SPLY|ROZDEL|PREMEN|ZRUS|ZANIK|_PF\b|_PF_")),
    ("other", re.compile(r"^OV_OPRAVA|^OV_OZNAMENIE_")),
]
CATEGORY_NAMES = {
    "liquidation": "likvidácia (liquidation, creditors called to file claims)",
    "dissolution": "zrušenie, zlúčenie, splynutie, rozdelenie, zmena právnej formy (dissolution, merger, split, conversion)",
    "capital": "zníženie základného imania, kapitálové fondy (capital reduction, capital funds)",
    "insolvency": "konkurz, reštrukturalizácia, oznámenia správcov (bankruptcy, restructuring, trustee notices)",
    "register": "podanie do obchodného registra (commercial register entry: new, change or deletion)",
    "other": "iné oznámenie alebo oprava (other notice or correction)",
}
# Insolvency notices moved to REPLIK on 1 October 2025; later OV copies are not kept.
INSOLVENCY_UNTIL = "2025-09-30"
OR_CHANGE_TYPES = {"nz": "nový zápis (new entry)", "zm": "zmena (change)", "vy": "výmaz (deletion)",
                   "zz": "zmena (change)", "vz": "výmaz (deletion)"}

KIND = re.compile(r"-(OV_[A-Z0-9_]+?)(?:-v-\d+)")
ICO_TAGS = {"Ico", "ICO"}
NAME_TAGS = ("ObchodneMenoNazov", "ObchodneMeno", "Nazov", "Subjekt")
# Parties other than the notice's subject: their IČOs are not indexed as the subject's.
ROLE_TAGS = {
    "Navrhovatel", "Navrhovatelia", "Spravca", "Spravcovia", "Likvidator", "Likvidatori", "LikvidatorPO",
    "Veritel", "Veritelia", "PohladavkyPrihlasenie", "PohladavkyAdresaPO", "AdresaNaPrihlaseniePohladavok",
    "Spolocnici", "SpolocniciSubjektu", "Statutari", "Zastupcovia", "OdovzdavateliaImania",
    "PreberateliaImania", "DozornaRada", "Osoby", "Konatelia", "DPS", "Autor", "Vyhlasovatel",
}
TITLE_TAGS = ("TypZverejnenia", "NaZaklade", "ZbierkaListinTyp", "DruhPodania", "Druh", "TypNazov",
              "ZverejnenieOznamenia", "PodanieTyp")
TEXT_MIN = 200  # a leaf this long is free text, not a field
TEXT_IN_TEXT = re.compile(r"IČO\s*:?\s*((?:\d[  ]?){6,8})(?!\d)")
LIMITS = {"register": (300, 0), "insolvency": (1000, 600)}  # (text, details) characters
DEFAULT_LIMITS = (2000, 1200)
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, recover=True)


def kind_of(file_name: str | None) -> str | None:
    m = KIND.search(file_name or "")
    return m.group(1) if m else None


def category_of(kind: str | None) -> str | None:
    if not kind:
        return None
    if kind.endswith("ZBL_ZOZNAM"):  # bulk lists of ~200 companies that filed deeds, no content
        return None
    return next((name for name, pattern in CATEGORIES if pattern.search(kind)), None)


def normalize_ico(value: str | None) -> str | None:
    """'31 393 781' -> '31393781'; a short Slovak IČO is zero-padded. Anything else (a birth date in
    the IČO field, a foreign ID) is None."""
    if not value:
        return None
    digits = re.sub(r"[\s ]", "", value)
    if not digits.isdigit() or not 6 <= len(digits) <= 8 or set(digits) == {"0"}:
        return None
    return digits.zfill(8)


DELETED = re.compile(r"bola vymazaná[^.]*\.|Deň výmazu[^\n]*?\d{1,2}\.\s?\d{1,2}\.\s?\d{4}", re.I)
DISSOLVED = re.compile(r"PRÁVNY DÔVOD ZRUŠENIA\s*:?\s*\n?([^\n]{3,300})")


def register_summary(kind: str, text: str) -> str | None:
    """The part of a register filing worth keeping: deposited documents, or why it was deleted."""
    if not text:
        return None
    if "uložila tieto listiny" in text or "ZBL" in kind:
        docs = [line.lstrip("- ").strip() for line in text.splitlines() if line.startswith("-")]
        return "Uložené listiny: " + "; ".join(docs) if docs else None
    parts = [m.group(0).strip() for m in DELETED.finditer(text)]
    reason = DISSOLVED.search(text)
    if reason:
        parts.append("Dôvod zrušenia: " + reason.group(1).strip())
    return " ".join(parts) or None


def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _text(el) -> str:
    return re.sub(r"\s+", " ", (el.text or "")).strip()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " […]"


def _name_near(ico_el) -> str | None:
    """The name next to an IČO: in its parent, or one level further up."""
    for scope in (ico_el.getparent(), ico_el.getparent().getparent() if ico_el.getparent() is not None else None):
        if scope is None:
            continue
        for tag in NAME_TAGS:
            found = [e for e in scope.iter() if _local(e) == tag and _text(e)]
            if found:
                return _text(found[-1])  # OR extracts list former names first
    return None


def _in_role(el) -> bool:
    return any(_local(a) in ROLE_TAGS for a in el.iterancestors())


def parse(raw_id: int, file_name: str, content: str | None, published: str, issue: str) -> dict | None:
    """A notice record with its subject IČOs, or None when the kind is not kept or no IČO is found.

    published: YYYY-MM-DD of the Vestník issue; issue: "190/2026".
    """
    kind = kind_of(file_name)
    category = category_of(kind)
    if not category or not content or (category == "insolvency" and published > INSOLVENCY_UNTIL):
        return None
    try:
        root = etree.fromstring(content.lstrip("﻿").encode(), _PARSER)
    except (etree.XMLSyntaxError, ValueError):
        return None
    if root is None:
        return None

    subjects: dict[str, str | None] = {}
    for el in root.iter():
        if _local(el) in ICO_TAGS and not _in_role(el):
            ico = normalize_ico(_text(el))
            if ico and ico not in subjects:
                subjects[ico] = _name_near(el)

    title = None
    for tag in TITLE_TAGS:
        found = next((e for e in root.iter() if _local(e) == tag and _text(e)), None)
        if found is not None:
            title = _text(found)
            break
    if category == "register" and root.get("typZmeny"):
        title = OR_CHANGE_TYPES.get(root.get("typZmeny"), root.get("typZmeny"))

    texts, details = [], []
    for el in root.iter():
        if not isinstance(el.tag, str) or len(el) or not (el.text or "").strip():
            continue
        tag, value = _local(el), el.text.strip().replace("T00:00:00", "")
        if value.startswith("0001-01-01"):
            continue  # the forms' "no date"
        if len(value) >= TEXT_MIN or tag in ("ZverejnujeText", "TextPodania", "Text"):
            text = html_text(value)
            if text:
                texts.append(text)
        elif tag not in ICO_TAGS and tag not in TITLE_TAGS and tag not in NAME_TAGS:
            details.append(f"{tag}: {re.sub(r'\s+', ' ', value)}")
    text = "\n\n".join(texts)

    # Old OR filings (2016-2018) are one text: IČO and name are in it.
    if category == "register" and not subjects and text:
        m = TEXT_IN_TEXT.search(text)
        ico = normalize_ico(m.group(1)) if m else None
        if ico:
            name = re.search(r"OBCHODNÉ MENO:\s*(.+)", text)
            subjects[ico] = name.group(1).strip() if name else None

    mentioned: list[str] = []
    if category in ("dissolution", "capital", "liquidation", "other"):
        for m in TEXT_IN_TEXT.finditer(text):
            ico = normalize_ico(m.group(1))
            if ico and ico not in subjects and ico not in mentioned:
                mentioned.append(ico)
    if not subjects and not mentioned:
        return None

    text_limit, details_limit = LIMITS.get(category, DEFAULT_LIMITS)
    if category == "register":
        # Register filings repeat the whole entry; keep only what says what happened (the
        # register's history itself is in RPO). That keeps ~2.5 million filings small.
        text, details = register_summary(kind, text), []
        if "ZBL" in kind and title in (None, "Nové zápisy"):
            title = "uloženie listín do zbierky listín (deeds filed)"
    detail_text = "; ".join(details)
    return {
        "id": raw_id,
        "date": published,
        "issue": issue,
        "kind": kind,
        "category": category,
        "title": title or kind.removeprefix("OV_").replace("_", " ").lower(),
        "companies": [{"ico": ico, "name": name} for ico, name in subjects.items()],
        "mentioned_icos": mentioned,
        "text": _clip(text, text_limit) if text else None,
        "details": _clip(detail_text, details_limit) if detail_text else None,
    }
