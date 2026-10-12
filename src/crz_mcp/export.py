"""Parser for the CRZ daily change exports (https://www.crz.gov.sk/export/YYYY-MM-DD.zip).

Each export is a ZIP with one XML file listing every contract record that changed on that day.
The data is licensed CC BY 4.0 by Úrad vlády SR.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

from .parse import BASE_URL


def _blank_date(value: str | None) -> str | None:
    if not value or value.startswith("0000-00-00"):
        return None
    return value.replace(" ", "T")


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _attachments(el: ET.Element) -> list[dict]:
    out = []
    for p in el.iterfind("prilohy/priloha"):
        name = (p.findtext("nazov") or "").strip()
        # dokument = scan, dokument1 = text version; either can be empty.
        for kind, file_tag, size_tag in (("sken", "dokument", "velkost"), ("text", "dokument1", "velkost1")):
            filename = (p.findtext(file_tag) or "").strip()
            if filename:
                out.append({
                    "name": name,
                    "kind": kind,
                    "url": f"{BASE_URL}/data/att/{filename}",
                    "size_bytes": int(p.findtext(size_tag) or 0) or None,
                })
    return out


def _record(el: ET.Element) -> dict:
    t = lambda tag: (el.findtext(tag) or "").strip()  # noqa: E731
    contract_id = int(t("ID"))
    confirmation = t("potvrdenie")
    return {
        "id": contract_id,
        "url": f"{BASE_URL}/index.php?ID={contract_id}",
        "number": t("nazov") or None,
        "subject": t("predmet") or None,
        "description": t("popis_predmetu") or t("popis") or None,
        "note": t("poznamka") or None,
        "buyer": {"name": t("zs1") or None, "ico": t("ico1") or None, "address": t("sidlo1") or None},
        "supplier": {"name": t("zs2") or None, "ico": t("ico") or None, "address": t("sidlo") or None},
        "department_id": int(t("rezort")) if t("rezort").isdigit() else None,
        "amounts_eur": {"agreed": _float(t("suma_zmluva")), "total": _float(t("suma_spolu"))},
        "dates": {
            "concluded": _blank_date(t("datum")),
            "published": _blank_date(t("datum_zverejnene")),
            "effective": _blank_date(t("datum_ucinnost")),
            "valid_until": _blank_date(t("datum_platnost_do")),
            "changed": _blank_date(t("chan")),
        },
        "attachments": _attachments(el),
        "confirmation_url": f"{BASE_URL}/data/att/{confirmation}" if confirmation else None,
        # Raw code values; CRZ does not publish their meaning. Observed: druh 1 = contract, 2 = amendment.
        "codes": {k: t(k) for k in ("typ", "druh", "stav", "zdroj", "potv_ziadost")},
    }


def parse_export_xml(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    return [_record(el) for el in root.iterfind("zmluva")]


def parse_export_zip(data: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise ValueError("Export ZIP contains no XML file")
        return parse_export_xml(zf.read(names[0]))


def matches(record: dict, *, text: str | None = None, ico: str | None = None, department_id: int | None = None) -> bool:
    if department_id is not None and record["department_id"] != department_id:
        return False
    if ico:
        wanted = ico.lstrip("0")
        if wanted not in {(record["buyer"]["ico"] or "").lstrip("0"), (record["supplier"]["ico"] or "").lstrip("0")}:
            return False
    if text:
        needle = text.casefold()
        haystack = " ".join(
            filter(None, [
                record["number"], record["subject"], record["description"],
                record["buyer"]["name"], record["supplier"]["name"],
            ])
        ).casefold()
        if needle not in haystack:
            return False
    return True
