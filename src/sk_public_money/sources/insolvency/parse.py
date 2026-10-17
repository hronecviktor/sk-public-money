"""Turn REPLIK SOAP answers (already converted to dicts by client.to_dict) into compact records."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .client import as_list

# The konanie and oznam services spell the same types differently (ODDLZENIE_KONKURZ vs
# ODDLZENIE-KONKURZ, INE vs INEKONANIA); both are mapped to the konanie spelling.
TYPE_ALIASES = {"ODDLZENIE_SPLATKOVYKALENDAR": "ODDLZENIE_SPLATKOVY_KALENDAR", "INEKONANIA": "INE"}
TYPE_NAMES = {
    "KONKURZ": "konkurz (bankruptcy)",
    "MALYKONKURZ": "malý konkurz (small bankruptcy)",
    "RESTRUKTURALIZACIA": "reštrukturalizácia (restructuring)",
    "ODDLZENIE_KONKURZ": "oddlženie konkurzom (personal debt relief by bankruptcy)",
    "ODDLZENIE_SPLATKOVY_KALENDAR": "oddlženie splátkovým kalendárom (personal debt relief by repayment schedule)",
    "LIKVIDACIA": "likvidácia (liquidation)",
    "VPR": "verejná preventívna reštrukturalizácia (public preventive restructuring)",
    "INE": "iné konanie (other proceeding)",
}
TRUSTEE_ROLES = {"RIADNY": "trustee (správca)", "PREDBEZNY": "interim trustee (predbežný správca)", "DOZORNY": "supervising trustee (dozorný správca)"}
PERSON_KINDS = {"PravnickaOsoba": "legal_entity", "FyzickaOsobaPodnikatel": "sole_trader", "FyzickaOsoba": "person"}
NOTICE_KINDS = {"OZNAM_SUD": "court", "OZNAM_SPRAVCA": "trustee"}


def compact(record: dict) -> dict:
    """Drop empty values so records stay short."""
    return {k: v for k, v in record.items() if v not in (None, "", [], {})}


def type_code(value: str | None) -> str | None:
    if not value:
        return None
    code = value.replace("-", "_")
    return TYPE_ALIASES.get(code, code)


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _join(*parts: str | None) -> str | None:
    return " ".join(p.strip() for p in parts if p and p.strip()) or None


def address(a) -> str | None:
    if not isinstance(a, dict):
        return None
    numbers = "/".join(n for n in (a.get("SupisneCislo"), a.get("OrientacneCislo")) if n and n != "0")
    street = _join(a.get("Ulica"), numbers)
    town = _join(a.get("Psc"), a.get("Obec"))
    country = a.get("Krajina") if a.get("Krajina") not in (None, "SVK") else None
    return ", ".join(p for p in (street, town, country) if p) or None


def person(p) -> dict | None:
    """A debtor or petitioner, published as REPLIK gives it (birth dates included)."""
    if not isinstance(p, dict):
        return None
    kind = PERSON_KINDS.get(p.get("_type", ""), p.get("_type"))
    full_name = _join(p.get("TitulPredMenom"), p.get("Meno"), p.get("Priezvisko"))
    if p.get("TitulZaMenom"):
        full_name = f"{full_name}, {p['TitulZaMenom']}" if full_name else p["TitulZaMenom"]
    account = p.get("BankovyUcet") if isinstance(p.get("BankovyUcet"), dict) else {}
    return compact({
        "kind": kind,
        "name": p.get("ObchodneMeno") or full_name,
        "person": full_name if p.get("ObchodneMeno") else None,
        "ico": p.get("Ico"),
        "birth_date": p.get("DatumNarodenia"),
        "birth_number": p.get("RodneCislo"),
        "citizenship": p.get("StatneObcianstvo"),
        "legal_form": p.get("PravnaForma"),
        "register": _join(p.get("Register"), p.get("CisloRegistracie")),
        "address": address(p.get("Adresa")),
        "email": p.get("Email"),
        "phone": p.get("Telefon"),
        "iban": account.get("Iban"),
    })


def trustee(s) -> dict | None:
    if not isinstance(s, dict):
        return None
    full_name = _join(s.get("TitulPredMenom"), s.get("Meno"), s.get("Priezvisko"))
    if s.get("TitulZaMenom"):
        full_name = f"{full_name}, {s['TitulZaMenom']}"
    # A trustee who is a person has her surname in ObchodneMeno; a firm has its name there.
    name = full_name if s.get("Meno") else s.get("ObchodneMeno") or full_name
    record = {
        "name": name,
        "ico": s.get("Ico"),
        "trustee_mark": s.get("Znacka"),
        "listed_since": s.get("DatumZapisu"),
        "birth_date": s.get("DatumNarodenia"),
        "address": address(s.get("Adresa")),
        "email": s.get("Email"),
        "phone": s.get("Telefon"),
    }
    # A trustee firm that renamed itself keeps its old name in Priezvisko.
    if not s.get("Meno") and s.get("Priezvisko") and s.get("ObchodneMeno") and s["Priezvisko"] != s["ObchodneMeno"]:
        record["other_name"] = s["Priezvisko"]
    return compact(record)


def proceeding_info(k: dict) -> dict:
    """A KonanieInfo row: one line of a search result."""
    code = type_code(k.get("Typ"))
    return compact({
        "id": _int(k.get("Id")),
        "file_number": k.get("SpisovaZnackaSudu"),
        "trustee_file_number": k.get("SpisovaZnackaSpravcu"),
        "court": k.get("SudNazov"),
        "court_code": k.get("Sud"),
        "type": code,
        "type_name": TYPE_NAMES.get(code),
        "status": k.get("StavKonania"),
        "debtor": k.get("Dlznik"),
        "debtor_ico": k.get("DlznikIco"),
        "debtor_birth_date": k.get("DlznikDatumNarodenia"),
        "filed": k.get("DatumPodania"),
        "opened": k.get("DatumZacatiaKonania"),
        "declared": k.get("DatumZacatiaProcesu"),
        "ended": k.get("DatumUkonceniaProcesu"),
        "end_reason": k.get("DovodUkonceniaProcesu"),
        "last_event": k.get("PoslednaUdalost"),
        "last_event_date": k.get("DatumPoslednejUdalosti"),
        "trustee": k.get("Spravca"),
        "trustee_role": TRUSTEE_ROLES.get(k.get("TypSpravcu"), k.get("TypSpravcu")),
        "disputed_claims": _int(k.get("PocetPopreti")) or None,
    })


def proceeding(k: dict) -> dict:
    """A Konanie detail: parties, court, judge and trustee."""
    code = type_code(k.get("Typ"))
    court = k.get("Sud") if isinstance(k.get("Sud"), dict) else {}
    return compact({
        "id": _int(k.get("Id")),
        "file_number": k.get("SpisovaZnackaSudu"),
        "trustee_file_number": k.get("SpisovaZnackaSpravcu"),
        "court": court.get("nazov"),
        "court_code": court.get("id"),
        "judge": k.get("Sudca"),
        "type": code,
        "type_name": TYPE_NAMES.get(code),
        "scope": k.get("TypKonaniaPodlaUzemnejPlatnosti"),
        "debtor": person(k.get("Dlznik")),
        "petitioners": [p for p in map(person, as_list(k.get("Navrhovatel"))) if p],
        "opened": k.get("DatumZacatiaKonania"),
        "declared": k.get("DatumZacatiaProcesu"),
        "small_bankruptcy": k.get("MalyKonkurz") == "true" or None,
        "debt_relief_granted": k.get("DatumPovoleniaOddlzenia"),
        "supervision_since": k.get("DatumZavedeniaDozornejSpravy"),
        "trustee": trustee(k.get("Spravca")),
        "trustee_role": TRUSTEE_ROLES.get(k.get("TypSpravcu"), k.get("TypSpravcu")),
        "trustee_assignment": {"GENEROVANY": "drawn by lot", "VYMENOVANY": "appointed"}.get(k.get("TypPrideleniaSpravcu")),
    })


def notice_info(o: dict) -> dict:
    code = type_code(o.get("KonanieTyp"))
    return compact({
        "id": _int(o.get("OznamId")),
        "date": o.get("DatumVydania"),
        "by": NOTICE_KINDS.get(o.get("OznamTyp"), o.get("OznamTyp")),
        "court": o.get("SudNazov"),
        "court_code": o.get("SudKod"),
        "file_number": o.get("SpisovaZnackaSudnehoSpisu"),
        "proceeding_id": _int(o.get("KonanieId")),
        "proceeding_type": code,
    })


def _label(code: str | None) -> str | None:
    """OZNAM_O_ZVOLANI_SCHODZE_VERITELOV -> 'oznam o zvolani schodze veritelov'."""
    return code.replace("_", " ").lower() if code and code != "NEDEFINOVANE" else None


BLOCKS = ["p", "div", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6"]
_HTML = re.compile(r"<(p|br|div|span|table|strong|em|b|i|u|li|ul|ol|h\d)\b|&[a-z]+;|&#\d+;", re.I)


def html_text(value: str | None) -> str | None:
    """Notice text as plain text. Trustees' texts are HTML (with entities); courts' are plain."""
    if not value:
        return None
    if _HTML.search(value):
        soup = BeautifulSoup(value, "html.parser")
        # Line breaks only at block elements; inline tags (span, strong) run on in the same line.
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for block in soup.find_all(BLOCKS):
            block.insert_after("\n\n")
        value = soup.get_text()
    value = value.replace("\xa0", " ").replace("\r", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text or None


def clip(text: str | None, limit: int) -> tuple[str | None, bool]:
    if text is None or len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + " […]", True


def notice(o: dict, *, limit: int) -> dict:
    """A notice with its text: court notices in parts (header, decision, reasoning...), trustee
    notices as one text. Texts longer than limit characters in total are cut and marked."""
    record = notice_info(o)
    if o.get("TextDruh") or o.get("_type") == "VerejnyOznamSud":
        record["document"] = _label(o.get("TextDruh"))
        record["subject"] = o.get("PredmetRozhodnutia")
        parts = {
            "header": o.get("TextHlavicka"),
            "notice": o.get("TextOznam"),
            "decision": o.get("TextRozhodnutie"),
            "reasoning": o.get("TextOdovodnenie"),
            "instructions": o.get("TextPoucenie"),
        }
    else:
        record["document"] = _label(o.get("DruhPodania"))
        record["trustee_file_number"] = o.get("SpisovaZnackaSpravcovskehoSpisu")
        parts = {"text": o.get("Text")}
    budget, truncated = limit, False
    for key, raw in parts.items():
        text = html_text(raw)
        if not text:
            continue
        if budget <= 0:
            truncated = True
            continue
        text, cut = clip(text, budget)
        record[key] = text
        budget -= len(text)
        truncated = truncated or cut
    attachments = o.get("VerejnyOznamPrilohyList")
    record["attachments"] = [
        compact({"id": _int(a.get("PrilohaId")), "name": a.get("Nazov")})
        for a in as_list(attachments.get("VerejnyOznamPriloha") if isinstance(attachments, dict) else None)
        if isinstance(a, dict)
    ]
    if truncated:
        record["truncated"] = True
    return compact(record)
