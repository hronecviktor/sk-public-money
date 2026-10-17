"""Turn RPVS OData records into compact, readable dicts."""

from __future__ import annotations

from ... import history
from .client import WEB_URL

DELETION_REASONS = {"Dobrovolny": "voluntary", "ExOffo": "ex officio"}
PERSON_FORMS = {
    "PravnickaOsoba": "legal entity",
    "FyzickaOsoba": "natural person",
    "FyzickaOsobaPodnikatel": "sole trader",
}


def day(value: str | None) -> str | None:
    return value[:10] if value else None


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def person_name(rec: dict) -> str | None:
    """Full name with academic titles, or the business name for legal entities."""
    parts = [_clean(rec.get("TitulPred")), _clean(rec.get("Meno")), _clean(rec.get("Priezvisko"))]
    name = " ".join(p for p in parts if p)
    if name and _clean(rec.get("TitulZa")):
        name += ", " + _clean(rec.get("TitulZa"))
    return name or _clean(rec.get("ObchodneMeno"))


def address(rec: dict | None) -> str | None:
    if not rec:
        return None
    numbers = "/".join(n for n in (_clean(rec.get("SupisneCislo")), _clean(rec.get("OrientacneCislo"))) if n)
    street = " ".join(p for p in (_clean(rec.get("MenoUlice")), numbers) if p)
    town = " ".join(p for p in (_clean(rec.get("Psc")), _clean(rec.get("Mesto"))) if p)
    return ", ".join(p for p in (street, town) if p) or None


def web_url(registration_number: int | None) -> str | None:
    # The web register addresses partners by registration number (číslo vložky), not by API Id.
    return f"{WEB_URL}/Partner/Partner/Detail/{registration_number}" if registration_number else None


def partner_identity(rec: dict) -> dict:
    """One PartnerVerejnehoSektora record: the partner's name/IČO/address for a validity period."""
    out = {
        "name": person_name(rec) if rec.get("FormaOsoby") != "PravnickaOsoba" else _clean(rec.get("ObchodneMeno")),
        "ico": _clean(rec.get("Ico")),
        "form": PERSON_FORMS.get(rec.get("FormaOsoby"), rec.get("FormaOsoby")),
        "valid_from": day(rec.get("PlatnostOd")),
        "valid_to": day(rec.get("PlatnostDo")),
    }
    if rec.get("DatumNarodenia"):  # natural-person partners only
        out["birth_date"] = day(rec["DatumNarodenia"])
    if rec.get("PravnaForma"):
        out["legal_form"] = rec["PravnaForma"].get("Meno")
    if "Adresa" in rec:
        out["address"] = address(rec.get("Adresa"))
    return out


def latest(records: list[dict]) -> dict:
    """The current record (no end date), else the one that started last."""
    return max(records, key=lambda r: (r.get("PlatnostDo") is None, r.get("PlatnostOd") or ""))


def beneficial_owner(rec: dict) -> dict:
    out = {
        "name": person_name(rec),
        "birth_date": day(rec.get("DatumNarodenia")),
        "public_official": bool(rec.get("JeVerejnyCinitel")),
        "valid_from": day(rec.get("PlatnostOd")),
        "valid_to": day(rec.get("PlatnostDo")),
    }
    if _clean(rec.get("Ico")):
        out["ico"] = _clean(rec.get("Ico"))
    return out


def authorized_person(rec: dict) -> dict:
    return {
        "name": person_name(rec) if rec.get("FormaOsoby") != "PravnickaOsoba" else _clean(rec.get("ObchodneMeno")),
        "ico": _clean(rec.get("Ico")),
        "birth_date": day(rec.get("DatumNarodenia")),
        "valid_from": day(rec.get("PlatnostOd")),
        "valid_to": day(rec.get("PlatnostDo")),
    }


def public_official(rec: dict) -> dict:
    return {
        "name": person_name(rec),
        "valid_from": day(rec.get("PlatnostOd")),
        "valid_to": day(rec.get("PlatnostDo")),
    }


def _split(items: list[dict], include_history: bool) -> tuple[list[dict], list[dict] | int]:
    return history.split(items, [i["valid_to"] is not None for i in items], include_history)


def partner(identities: list[dict], entity: dict, *, include_history: bool = False) -> dict:
    """Combine a partner's identity records with its Partneri entity (expanded collections)."""
    owners, former_owners = _split([beneficial_owner(r) for r in entity.get("KonecniUzivateliaVyhod") or []], include_history)
    authorized, former_authorized = _split([authorized_person(r) for r in entity.get("OpravneneOsoby") or []], include_history)
    officials, former_officials = _split([public_official(r) for r in entity.get("VerejniFunkcionari") or []], include_history)

    current = partner_identity(latest(identities)) if identities else {}
    out: dict = {
        "partner_id": entity["Id"],
        "registration_number": entity.get("CisloVlozky"),
        "url": web_url(entity.get("CisloVlozky")),
        **current,
        "registered": entity.get("Vymaz") is None,
        "beneficial_owners": owners,
        "former_beneficial_owners": former_owners,
        "authorized_persons": authorized,
        "former_authorized_persons": former_authorized,
        "public_officials": officials,
        "former_public_officials": former_officials,
    }
    if include_history:
        out["identity_history"] = [partner_identity(r) for r in sorted(identities, key=lambda r: r.get("PlatnostOd") or "")]
    if entity.get("Vymaz"):
        v = entity["Vymaz"]
        out["deletion"] = {"date": day(v.get("Datum")), "reason": DELETION_REASONS.get(v.get("Dovod"), v.get("Dovod")), "note": _clean(v.get("Poznamka"))}
    if entity.get("Pokuta"):
        p = entity["Pokuta"]
        out["fine"] = {"date": day(p.get("DatumVytvorenia")), "note": _clean(p.get("Poznamka"))}
    complaints = entity.get("KvalifikovanePodnety") or []
    if complaints:
        out["qualified_complaints"] = [
            {
                "case_number": _clean(k.get("SpisovaZnackaKonania")),
                "started": day(k.get("DatumZacatiaKonania")),
                "ended": day(k.get("DatumSkonceniaKonania")),
                "decision_final": day(k.get("DatumPravoplatnostiRozhodnutia")),
            }
            for k in complaints
        ]
    verification = entity.get("OverenieIdentifikacieKUV")
    if verification:
        out["owners_verified"] = {"date": day(verification.get("DatumOverenia")), "valid": verification.get("Platny")}
    return out
