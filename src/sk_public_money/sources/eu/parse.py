"""ITMS21+ records -> compact, readable dicts.

The API inlines every codelist value (with Slovak and English names, validity and country), so a
list record is many times larger than its content. These functions keep only what the tools use:
names, IČOs, amounts, dates (as YYYY-MM-DD in Bratislava time) and the IDs that link records.
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ...http import TZ

TEXT_LIMIT = 3000


def date(ms) -> str | None:
    if ms in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, TZ).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def label(value) -> str | None:
    """A codelist value's Slovak name."""
    if isinstance(value, dict):
        return value.get("nazovSk") or value.get("nazov") or value.get("kod")
    return value


def ref(value) -> int | None:
    """The ID of a linked record."""
    return value.get("id") if isinstance(value, dict) else None


def html_text(value: str | None, limit: int = TEXT_LIMIT) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", BeautifulSoup(value, "html.parser").get_text(" ")).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " …"
    return text or None


def norm_ico(value) -> str | None:
    """IČO without spaces; Slovak IČOs typed without leading zeros are padded to 8 digits."""
    if value in (None, ""):
        return None
    ico = re.sub(r"\s+", "", str(value))
    return ico.zfill(8) if ico.isdigit() and len(ico) < 8 else ico


def address(a: dict | None) -> str | None:
    if not a:
        return None
    street = " ".join(p for p in (a.get("ulica"), a.get("cislo")) if p)
    town = " ".join(p for p in (a.get("psc"), a.get("obec")) if p)
    country = label(a.get("stat"))
    parts = [street, town] + ([country] if country and country != "Slovensko" else [])
    return ", ".join(p for p in parts if p) or None


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, [], "", {})}


def organisation(raw: dict) -> dict:
    """A subjekt or a dodavatelobstaravatel record (supplier or buyer outside the subjekt register)."""
    other = raw.get("ineIdentifikacneCislo")
    return _clean({
        "name": raw.get("nazov"),
        "ico": norm_ico(raw.get("ico")),
        "dic": raw.get("dic"),
        "ic_dph": raw.get("icDph"),
        "other_id": f"{label(raw.get('typInehoIdentifikatora')) or 'ID'}: {other}" if other else None,
        "legal_form": label(raw.get("pravnaForma")),
        "address": address(raw.get("adresa")),
    })


def _places(values) -> list[str]:
    return [label(v) for v in values or [] if label(v)]


def project_row(raw: dict) -> dict:
    """A projekt list record (linkApi)."""
    contract = raw.get("zmluvaProjekt") or {}
    return _clean({
        "id": raw["id"],
        "code": raw.get("kod"),
        "name": raw.get("nazov"),
        "status": raw.get("stav"),
        "call_id": ref(raw.get("vyzva")),
        "recipient_id": ref(raw.get("prijimatel")),
        "partner_ids": [p["id"] for p in raw.get("partner") or [] if p.get("id")],
        "application_id": ref(raw.get("zonfp")),
        "contracted": raw.get("celkovaZazmluvnenaSuma"),
        "start": date(raw.get("skutocnaRealizaciaZaciatok")),
        "end": date(raw.get("skutocnaRealizaciaKoniec")),
        "in_progress": raw.get("vrealizacii"),
        "finished": raw.get("ukonceny"),
        "excluded_from_funding": raw.get("vylucenyZFinancovania") or None,
        "focus": raw.get("zameranieProjektu"),
        "places": _places(raw.get("miestoRealizacie")),
        "contract": _clean({
            "number": contract.get("cislo"),
            "effective": date(contract.get("datumUcinnosti")),
            "url": contract.get("url"),
        }),
        "updated": date(raw.get("updatedAt") or raw.get("createdAt")),
    })


def application_row(raw: dict) -> dict:
    """A ŽoNFP (application for a non-repayable grant) list record (linkApi)."""
    return _clean({
        "id": raw["id"],
        "code": raw.get("kod"),
        "name": raw.get("nazov"),
        "status": raw.get("stav"),
        "call_id": ref(raw.get("vyzva")),
        "applicant_id": ref(raw.get("ziadatel")),
        "partner_ids": [p["id"] for p in raw.get("partner") or [] if p.get("id")],
        "requested_grant": raw.get("sumaZiadanaNFP"),
        "requested_total": raw.get("sumaZiadanaCelkova"),
        "approved_grant": raw.get("sumaSchvalenaNFP"),
        "approved_total": raw.get("sumaSchvalenaCelkova"),
        "approved": raw.get("schvalena"),
        "rejected": raw.get("neschvalena"),
        "submitted": date(raw.get("datumPredlozenia")),
        "focus": raw.get("zameranieProjektu"),
        "places": _places(raw.get("miestoRealizacie")),
    })


def procurement_row(raw: dict) -> dict:
    """A verejneobstaravanie list record (linkApi)."""
    return _clean({
        "id": raw["id"],
        "code": raw.get("kod"),
        "name": raw.get("nazov"),
        "buyer_id": ref(raw.get("obstaravatelSubjekt")),
        "buyer_supplier_id": ref(raw.get("dodavatelObstaravatel")),
        "buyer_type": label(raw.get("druhObstaravatelaVO")),
        "status": raw.get("stav"),
        "estimated_value": raw.get("predpokladanaHodnotaZakazky"),
        "updated_ms": raw.get("updatedAt") or raw.get("createdAt"),
    })


def claim_row(raw: dict) -> dict:
    """A ŽoP (payment claim) list record (linkApi)."""
    return _clean({
        "id": raw["id"],
        "code": raw.get("kod"),
        "project_id": ref(raw.get("projekt")),
        "recipient_id": ref(raw.get("prijimatel")),
        "submitted_for_id": ref(raw.get("predkladanaZa")),
        "lead_partner_id": ref(raw.get("hlavnyCezhranicnyPartner")),
        "amount": raw.get("narokovanaSuma"),
        "type": raw.get("typ"),
        "paid": (not raw["neuhradena"]) if isinstance(raw.get("neuhradena"), bool) else None,
        "received": date(raw.get("datumPrijatia")),
        "paid_on": date(raw.get("datumUhrady")),
    })


def invoice_row(raw: dict) -> dict:
    """An účtovný doklad (invoice or internal document claimed in a project) list record (linkApi)."""
    return _clean({
        "id": raw["id"],
        "number": (raw.get("cisloDokladu") or "").strip() or None,
        "name": raw.get("nazov"),
        "amount": raw.get("celkovaVyskaDokladu"),
        "type": raw.get("typ"),
        "owner_id": ref(raw.get("vlastnikDokladu")),
        "supplier_subject_id": ref(raw.get("dodavatelSubjekt")),
        "supplier_id": ref(raw.get("dodavatelDodavatelObstaravatel")),
        "created": date(raw.get("createdAt")),
    })


def _cpv(value) -> str | None:
    if not isinstance(value, dict):
        return None
    code = (value.get("kod") or "")[4:] if (value.get("kod") or "").startswith("1049") else value.get("kod")
    return f"{code} {value.get('nazovSk')}".strip() if value.get("nazovSk") else code


def contract_row(raw: dict, procurement_id: int) -> dict:
    return _clean({
        "id": raw["id"],
        "procurement_id": procurement_id,
        "code": raw.get("kod"),
        "name": raw.get("nazov"),
        "number": raw.get("cisloZmluvy"),
        "effective": date(raw.get("datumUcinnosti")),
        "supplier_id": ref(raw.get("hlavnyDodavatelDodavatelObstaravatel")),
        "supplier_subject_id": ref(raw.get("hlavnyDodavatelSubjekt")),
        "amount": raw.get("celkovaSumaZmluvy"),
    })


def procurement_detail(raw: dict) -> dict:
    """What a procurement's detail adds to its list record: subject, procedure, bids, Vestník
    references, linked projects and the signed contracts with their main supplier's ID."""
    duration = raw.get("trvanieZakazkyHodnota")
    return _clean({
        "kind": label(raw.get("druhZakazky")),
        "method": label(raw.get("metodaVO")),
        "procedure": label(raw.get("postupObstaravania")),
        "cpv": _cpv(raw.get("hlavnyPredmetHlavnySlovnik")),
        "cpv_more": [c for c in (_cpv(v) for v in raw.get("doplnujucePredmetyHlavnySlovnik") or []) if c],
        "award_criterion": raw.get("kriteriumNaVyhodnoteniePonuk"),
        "tender_deadline": date(raw.get("lehotaNaPredkladaniePonuk")),
        "tenders_received": raw.get("pocetPrijatychPonuk"),
        "tenders_excluded": raw.get("pocetVylucenychPonuk"),
        "central_procurement": raw.get("centralneObstaravanie") or None,
        "split_into_lots": raw.get("zakazkaRozdelenaNaViacCasti") or None,
        "duration": f"{duration:g} {raw.get('trvanieZakazkyMernaJednotka', '').lower()}".strip() if duration else None,
        "vestnik": _clean({
            "issue": raw.get("cisloVestnika"),
            "number": raw.get("cisloZverejneniaVoVestniku"),
            "published": date(raw.get("datumZverejnenia")),
            "url": raw.get("urlOdkazOznamenie"),
        }),
        "eu_journal": _clean({
            "issue": raw.get("cisloVestnikaEU"),
            "number": raw.get("cisloZverejneniaVoVestnikuEU"),
            "published": date(raw.get("datumZverejneniaVoVestnikuEU")),
            "url": raw.get("urlOdkazOznamenieEU"),
        }) if raw.get("zverejneneVoVestnikuEU") else None,
        "programmes": [p.get("skratka") or p.get("kod") for p in raw.get("programy") or []],
        "project_ids": [p["id"] for p in raw.get("projekty") or [] if p.get("id")],
        "contracts": [contract_row(c, raw["id"]) for c in raw.get("zmluvyVO") or [] if c.get("id")],
    })


def _indicator(item: dict) -> dict:
    sc = item.get("ukazovatelProjektovySC") or {}
    ind = sc.get("ukazovatelProjektovy") or {}
    return _clean({"name": ind.get("nazovSk"), "unit": label(ind.get("mernaJednotka")), "target": item.get("cielovaHodnotaSpolu")})


def project_detail(raw: dict) -> dict:
    """A projekt detail (linkApi) -> the fields eu_get_project shows; organisations stay as IDs for
    the caller to resolve."""
    contract = raw.get("zmluvaProjekt") or {}
    return _clean({
        "id": raw["id"],
        "code": raw.get("kod"),
        "name": raw.get("nazov"),
        "acronym": raw.get("akronym"),
        "status": raw.get("stav"),
        "programme_id": ref(raw.get("program")),
        "call_id": ref(raw.get("vyzva")),
        "recipient_id": ref(raw.get("prijimatel")),
        "partner_ids": [p["id"] for p in raw.get("partner") or [] if p.get("id")],
        "application_id": ref(raw.get("zonfp")),
        "provider_id": ref(raw.get("poskytovatelSubjekt")),
        "managing_authority": (raw.get("poskytovatelOrgan") or {}).get("nazov"),
        "specific_objectives": [label(s) for s in raw.get("specifickyCielProgramu") or [] if label(s)],
        "measures": [label(s) for s in raw.get("opatrenie") or [] if label(s)],
        "amounts": _clean({
            "contracted_total": raw.get("celkovaZazmluvnenaSuma"),
            "contracted_total_original": raw.get("celkovaZazmluvnenaSumaPovodna"),
            "contracted_grant": raw.get("zazmluvnenaSumaNfp"),
            "paid_to_recipient": raw.get("poskytnuteProstriedky"),
            "by_source": [_clean({"source": label(f.get("zdroj")), "amount": f.get("suma")}) for f in raw.get("financnyPlan") or []],
        }),
        "contract": _clean({
            "number": contract.get("cislo"),
            "valid": date(contract.get("datumPlatnosti")),
            "effective": date(contract.get("datumUcinnosti")),
            "url": contract.get("url"),
        }),
        "dates": _clean({
            "planned_start": date(raw.get("planovanaRealizaciaZaciatok")),
            "planned_end": date(raw.get("planovanaRealizaciaKoniec")),
            "actual_start": date(raw.get("skutocnaRealizaciaZaciatok")),
            "actual_end": date(raw.get("skutocnaRealizaciaKoniec")),
        }),
        "in_progress": raw.get("vrealizacii"),
        "finished": raw.get("ukonceny"),
        "excluded_from_funding": raw.get("vylucenyZFinancovania") or None,
        "open_amendment": raw.get("jeOtvorenaZmena") or None,
        "places": [
            ", ".join(p for p in (label(m.get("nuts5")), label(m.get("nuts4")), label(m.get("nuts3"))) if p)
            for m in raw.get("miestoRealizacieFull") or []
        ] or _places(raw.get("miestoRealizacie")),
        "description": html_text(raw.get("popis")),
        "purpose": html_text(raw.get("ucel")),
        "activities": [a.get("nazov", "").strip() for a in raw.get("aktivity") or [] if a.get("nazov")],
        "output_indicators": [_indicator(i) for i in raw.get("ukazovatelVystupu") or []],
        "result_indicators": [_indicator(i) for i in raw.get("ukazovatelVysledku") or []],
        "amendments": [
            _clean({"effective": date(z.get("datumUcinnosti")), "subject": html_text(z.get("predmet"), 500)})
            for z in raw.get("zmenaProjekt") or []
        ],
        "project_suppliers": [_clean({"name": d.get("nazov"), "ico": norm_ico(d.get("ico")), "dic": d.get("dic"),
                                      "other_id": d.get("ineIdentifikacneCislo")}) for d in raw.get("dodavatel") or []],
    })
