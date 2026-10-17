"""Turn ISU API records and Súdna rada pages into compact, readable dicts."""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

from ...text import fold
from .client import SUDNARADA_URL

# Values of the API's oblast/podOblast facets (právna oblasť), as of October 2026.
AREAS = ["Občianske právo", "Rodinné právo", "Obchodné právo", "Trestné právo", "Správne právo"]
SUBAREAS = [
    "Ostatné", "Iné", "Exekúcia a výkon rozhodnutí", "Spotrebiteľské zmluvy", "Spätvzatie",
    "Vyživovacie povinnosti", "Zmluvy", "Starostlivosť o\xa0maloletých", "Rozvod", "Majetok", "Konkurz",
    "Obchodné záväzkové vzťahy", "Život a\xa0zdravie", "Poistenie",
    "Všeobecne nebezpečné a\xa0proti životnému prostrediu", "Rodina a\xa0mládež",
    "Neodstránitelné podmienky konania", "Obchodné spoločnosti", "Spoločnosť s\xa0ručením obmedzeným",
    "Neodstránené podmienky konania", "Príslušnost", "Predbežné opatrenia", "Poriadok vo verejných veciach",
    "Žaloby proti právoplatným rozhodnutiam a postupom správnych orgánov", "Iné práva a\xa0slobody",
    "Neodkladné opatrenia", "Zodpovednosť za škodu pri výkone verejnej moci", "Kúpna zmluva",
    "Sloboda a\xa0ľudská dôstojnosť", "Schválenie právneho úkonu", "Odmietnutie", "Zmenky",
    "Bezdôvodné obohatenie", "Vlastnícke právo k\xa0nehnuteľnostiam", "Zodpovednosť za škodu",
    "Bezpodielové spoluvlastníctvo manželov", "Opatrovníctvo", "Dôchodky", "Nájomná zmluva",
    "Ústavná starostlivosť", "Výchovné opatrenia", "Zmluva o\xa0dielo", "Hospodárske trestné činy",
    "Spoluvlastníctvo", "Pracovné právo", "Incidenčné spory", "Vlastníctvo bytov a\xa0nebytových priestorov",
    "Neplatnosť právnych úkonov", "Opravné prostriedky proti neprávoplatným rozhodnutiam správnych orgánov",
    "Zmluva o úvere", "Osvojenie", "Ochrana osobnosti", "Povolenie uzavrieť manželstvo",
    "Nájom a\xa0podnájom nebytových priestorov", "Spotrebiteľské zmluvy – neplatná podmienka vo výroku",
    "Kataster nehnuteľností", "Náhrada škody", "Neplatnosť skončenia pracovného pomeru", "Akciová spoločnosť",
    "Pestúnska starostlivosť", "Zmluva o dielo", "Dedičské právo", "Vlastnícke právo k\xa0hnuteľným veciam",
    "Vyhlásenie za mŕtveho", "Darovacia zmluva", "Vydržanie", "Zabezpečovacie opatrenia", "Oddĺženie",
    "Reštrukturalizácia", "Družstvo", "Právo duševného vlastníctva", "Záložné právo",
    "Nečinnosti orgánu verejnej správy", "Cenné papiere", "Trestné činy proti mieru", "Dedenie zo závetu",
    "Volebné veci", "Nezákonný zásah orgánu verejnej správy", "Trestné činy proti republike",
    "Reštitúcie a\xa0rehabilitácie", "Vojenské trestné činy", "Vyrovnanie",
    "Abstraktná kontrola v spotrebiteľských veciach", "Trestné činy proti brannosti", "Šeky", "Dočasná ochrana",
    "Neverejná preventívna reštrukturalizácia",
]
JUDGE_STATUS = {
    "label.sudca.aktivny": "active",
    "label.sudca.odvolany": "removed from office (odvolaný)",
    "label.sudca.vymazany": "deleted from the register (vymazaný)",
    "label.sudca.prerusenie vykonu - poberatel": "office suspended (receives benefits)",
    "label.sudca.prerusenie vykonu - ina funkce": "office suspended (holds another function)",
}
FACETS = {
    "oblast_pravnej_upravy": "by_area",
    "forma_rozhodnutia": "by_form",
    "sud_typ": "by_court_type",
}


def _key(text: str) -> str:
    return " ".join(fold(text).split())


def match_area(area: str) -> tuple[str, str]:
    """('oblast' | 'podoblast', exact API value) for an area typed with any case, diacritics or spaces."""
    wanted = _key(area)
    for kind, values in (("oblast", AREAS), ("podoblast", SUBAREAS)):
        for value in values:
            if _key(value) == wanted:
                return kind, value
    candidates = [v for v in AREAS + SUBAREAS if wanted in _key(v)]
    if len(candidates) == 1:
        kind = "oblast" if candidates[0] in AREAS else "podoblast"
        return kind, candidates[0]
    hint = ", ".join(v.replace("\xa0", " ") for v in (candidates or AREAS))
    raise ValueError(f"Unknown area {area!r}. {'Did you mean: ' if candidates else 'Main areas: '}{hint}.")


def iso_date(value: str | None) -> str | None:
    """'30.06.2026' -> '2026-06-30'."""
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", (value or "").strip())
    return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}" if m else (value or None)


def snippet(value: str) -> str:
    """A search highlight as plain text, with the matched words marked «like this»."""
    value = re.sub(r'<span class="highlight-substring">(.*?)</span>', r"«\1»", value, flags=re.S)
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    return " ".join(value.replace(" ", " ").split())


def _ref(obj: dict | None, name_key: str = "nazov") -> tuple[str | None, str | None]:
    obj = obj or {}
    return obj.get(name_key) or None, obj.get("registreGuid") or None


def decision(rec: dict) -> dict:
    """A list or detail record of a decision (rozhodnutie)."""
    court, court_id = _ref(rec.get("sud"))
    judge, judge_id = _ref(rec.get("sudca"), "meno")
    out = {
        "guid": rec.get("guid"),
        "date": iso_date(rec.get("datumVydania")),
        "form": rec.get("formaRozhodnutia"),
        "court": court,
        "court_id": court_id,
        "judge": judge,
        "judge_id": judge_id,
        "case_number": rec.get("spisovaZnacka"),
        "file_number": rec.get("identifikacneCislo"),
    }
    if rec.get("povaha"):
        out["nature"] = rec["povaha"]
    if rec.get("ecli"):
        out["ecli"] = rec["ecli"]
    if rec.get("oblast"):
        out["area"] = rec["oblast"]
    if rec.get("podOblast"):
        out["subarea"] = [v.replace("\xa0", " ") for v in rec["podOblast"]]
    if rec.get("odkazovanePredpisy"):
        out["cited_laws"] = [
            {"law": p.get("nazov"), "url": p.get("url")} for p in rec["odkazovanePredpisy"]
        ]
    doc = rec.get("dokument")
    if doc and doc.get("url"):
        out["pdf"] = {"url": doc["url"], "name": doc.get("name"), "bytes": doc.get("size")}
    if rec.get("updateDate"):
        out["updated"] = iso_date(rec["updateDate"])
    if rec.get("zvyraznenie"):
        out["snippets"] = [snippet(z) for z in rec["zvyraznenie"]]
    return out


def facets(data: dict) -> dict:
    out = {}
    for f in data.get("filterList") or []:
        key = FACETS.get(f.get("filterName"))
        if key:
            out[key] = {v["text"].replace("\xa0", " "): v["count"] for v in f.get("facetValueList") or []}
    return out


def judge(rec: dict) -> dict:
    court, court_id = _ref(rec.get("sud"))
    out = {
        "judge_id": rec.get("registreGuid"),
        "name": rec.get("meno"),
        "function": rec.get("funkcia"),
        "status": JUDGE_STATUS.get(rec.get("stav"), rec.get("stav")),
        "court": court,
        "court_id": court_id,
    }
    temp = rec.get("docasnePridelenySud") or {}
    if temp.get("nazov"):
        out["temporarily_assigned_to"] = temp["nazov"]
        if rec.get("docasnePridelenySudFunkcia"):
            out["temporary_function"] = rec["docasnePridelenySudFunkcia"]
    for key, label in (("docasnePridelenePracovisko", "temporary_workplace"), ("docasnePridelenaOrganizacia", "temporary_organization")):
        if (rec.get(key) or {}).get("nazov"):
            out[label] = rec[key]["nazov"]
    if rec.get("poznamka"):
        out["note"] = rec["poznamka"]
    return out


def declarations(page_html: str) -> tuple[list[dict], int | None, bool]:
    """Rows of Súdna rada's asset-declaration list: (rows, total records, whether a next page exists)."""
    soup = BeautifulSoup(page_html, "lxml")
    total = None
    counter = soup.select_one(".mps-table__records b")
    if counter and counter.get_text(strip=True).isdigit():
        total = int(counter.get_text(strip=True))
    rows = []
    for item in soup.select("li.mps-table__item"):
        name = " ".join(item.select_one(".mps-table__name").get_text(" ").split()) if item.select_one(".mps-table__name") else None
        court = item.select_one(".mps-table__court")
        for link in item.select(".mps-table__att a[href]"):
            label = " ".join(link.get_text(" ").split())
            year = re.search(r"\b(19|20)\d\d\b", link.get("title") or label)
            size = re.search(r"\(\.?pdf,\s*([^)]*)\)", label, re.I)
            rows.append({
                "name": name,
                "court": " ".join(court.get_text(" ").split()) if court else None,
                "year": int(year[0]) if year else None,
                "url": SUDNARADA_URL + link["href"] if link["href"].startswith("/") else link["href"],
                "size": size[1] if size else None,
            })
    return rows, total, _next_page_link(soup) is not None


def _next_page_link(soup: BeautifulSoup) -> str | None:
    current = soup.select_one(".pagination .page-link .sr-only")
    m = re.search(r"(\d+)", current.get_text()) if current else None
    if not m:
        return None
    wanted = f"Stránka {int(m[1]) + 1}"
    link = soup.select_one(f".pagination a.page-link[aria-label='{wanted}']")
    return link["href"] if link else None


def name_matches(full_name: str, query: str) -> bool:
    """Every word of query appears in full_name as a word start, ignoring case, diacritics and order."""
    words = _key(full_name).replace(",", " ").replace(".", ". ").split()
    return all(any(w.startswith(q) for w in words) for q in _key(query).split())
