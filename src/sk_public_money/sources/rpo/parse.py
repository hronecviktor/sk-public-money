"""Turn RPO API records into compact, readable dicts."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ...http import TZ

# Placeholders RPO uses for unknown values, mostly on foreign companies.
UNKNOWN_ICO = {"Neuvedené", ""}
UNKNOWN_DATE = {"1800-01-01"}
HOME_COUNTRY = "703"  # Slovenská republika in codelist CL000086
MAX_ACTIVITIES = 30


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def _clean(value) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else value
    return value or None


def _date(value: str | None) -> str | None:
    return None if not value or value in UNKNOWN_DATE else value


def ico(value: str | None) -> str | None:
    value = _clean(value)
    return None if value is None or value in UNKNOWN_ICO else value


def is_current(rec: dict, on: str) -> bool:
    """Valid on the given day: started (or no start) and not yet ended."""
    return (rec.get("validTo") or "9999") >= on and (rec.get("validFrom") or "") <= on


def last_day(rec: dict, on: str) -> str:
    """The day to show an organization as of: on, or for a dissolved one the day before dissolution,
    when RPO ends its people and facts."""
    end = _date(rec.get("termination"))
    if end and end <= on:
        return (date.fromisoformat(end) - timedelta(days=1)).isoformat()
    return on


def _by_start(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: r.get("validFrom") or "")


def current_value(records: list[dict] | None, on: str):
    """The latest-starting entry valid on the given day, else the latest-starting entry overall."""
    records = _by_start(records or [])
    valid = [r for r in records if is_current(r, on)]
    return (valid or records or [None])[-1]


def code_value(value: dict | None) -> str | None:
    return _clean(value.get("value")) if value else None


def address(rec: dict | None) -> str | None:
    if not rec:
        return None
    if _clean(rec.get("formatedAddress")):
        return rec["formatedAddress"].strip()
    numbers = "/".join(str(n) for n in (rec.get("regNumber") or None, _clean(rec.get("buildingNumber"))) if n)
    street = " ".join(p for p in (_clean(rec.get("street")), numbers) if p)
    town = " ".join(p for p in (", ".join(rec.get("postalCodes") or []) or None, code_value(rec.get("municipality"))) if p)
    country = rec.get("country") or {}
    parts = [street, town]
    if country.get("code") != HOME_COUNTRY:
        parts.append(code_value(country))
    return ", ".join(p for p in parts if p) or None


def person_name(rec: dict | None) -> str | None:
    """Formatted name with titles, built from its parts when RPO has no formatted form."""
    if not rec:
        return None
    if _clean(rec.get("formatedName")):
        return rec["formatedName"].strip()
    before = " ".join(code_value(p) or "" for p in rec.get("prefixes") or [])
    after = ", ".join(code_value(p) or "" for p in rec.get("postfixes") or [])
    name = " ".join(p for p in (before, *(rec.get("givenNames") or []), *(rec.get("familyNames") or [])) if p)
    return (name + (", " + after if after else "")) or None


def _timed(rec: dict) -> dict:
    return {"valid_from": rec.get("validFrom"), "valid_to": rec.get("validTo")}


def party(rec: dict) -> dict:
    """A statutory body member or stakeholder: a person (personName) or a company (fullName/identifier)."""
    out: dict = {"role": code_value(rec.get("stakeholderType"))}
    if code_value(rec.get("statutoryBodyMember")):
        out["function"] = code_value(rec["statutoryBodyMember"])
    if rec.get("personName"):
        out["name"] = person_name(rec["personName"])
        born_as = rec["personName"].get("givenFamilyNames")
        if born_as and born_as != rec["personName"].get("familyNames"):
            out["birth_surname"] = " ".join(born_as)
    else:
        out["name"] = _clean(rec.get("fullName"))
        out["ico"] = ico(rec.get("identifier"))
        if _date(rec.get("establishment")):
            out["established"] = rec["establishment"]
    out["address"] = address(rec.get("address"))
    out.update(_timed(rec))
    return out


def _money(value, currency: dict | None) -> str | None:
    if value is None:
        return None
    amount = f"{value:,.2f}".replace(",", " ").removesuffix(".00")
    code = (currency or {}).get("code") or code_value(currency)
    return f"{amount} {code}" if code else amount


def equity(rec: dict) -> dict:
    cur = rec.get("currency")
    out = {k: _money(rec.get(src), cur) for k, src in (("registered", "value"), ("paid", "valuePaid"), ("approved", "valueApproved")) if rec.get(src) is not None}
    return {**out, **_timed(rec)}


def deposit(rec: dict) -> dict:
    """A partner's capital contribution (vklad) in an s.r.o. or similar."""
    return {
        "owner": person_name(rec.get("personName")) or _clean(rec.get("fullName")),
        "amount": _money(rec.get("amount"), rec.get("currency")),
        "type": _clean(rec.get("type")),
        **_timed(rec),
    }


def share(rec: dict) -> dict:
    out = {
        "type": code_value(rec.get("shareType")),
        "form": _clean(rec.get("shareForm")),
        "nominal_value": _money(rec.get("shareNominalValue"), rec.get("shareCurrency")),
        "count": rec.get("amountOfFunds"),
        "transferability": _clean(rec.get("transferability")),
    }
    owner = person_name(rec.get("personName")) or _clean(rec.get("fullName"))
    if owner:
        out["owner"] = owner
    return {**{k: v for k, v in out.items() if v is not None}, **_timed(rec)}


def related(rec: dict) -> dict:
    """A legal predecessor or successor."""
    return {"name": _clean(rec.get("fullName")), "ico": ico(rec.get("identifier")), "address": address(rec.get("address")), **_timed(rec)}


def _split(items: list[dict], records: list[dict], on: str, include_history: bool) -> tuple[list[dict], int]:
    current = [i for i, r in zip(items, records) if is_current(r, on)]
    former = [i for i, r in zip(items, records) if not is_current(r, on)]
    return (current + former if include_history else current), len(former)


def source_register(rec: dict | None, on: str) -> dict | None:
    if not rec:
        return None
    office = current_value(rec.get("registrationOffices"), on)
    number = current_value(rec.get("registrationNumbers"), on)
    return {
        "name": code_value(rec.get("value")),
        "office": office and office.get("value"),
        "number": number and number.get("value"),
    }


def summary(rec: dict, on: str | None = None) -> dict:
    """The identity part every RPO record has, as /search returns it."""
    on = on or today()
    name = current_value(rec.get("fullNames"), on)
    ident = current_value(rec.get("identifiers"), on)
    return {
        "rpo_id": rec["id"],
        "ico": ico(ident and ident.get("value")),
        "name": name and name.get("value"),
        "address": address(current_value(rec.get("addresses"), on)),
        "established": _date(rec.get("establishment")),
        "terminated": _date(rec.get("termination")),
        "source_register": source_register(rec.get("sourceRegister"), on),
    }


def organization(rec: dict, *, include_history: bool = False, on: str | None = None) -> dict:
    """A full /entity record (fetched with history) as current state, plus history when asked."""
    now = on or today()
    on = last_day(rec, now)
    out = summary(rec, on)
    if on != now:
        out["shown_as_of"] = on
    legal_form = current_value(rec.get("legalForms"), on)
    out["legal_form"] = legal_form and code_value(legal_form.get("value"))
    # Statuses such as "Zaniknutá spoločnosť" start on the dissolution day itself.
    statuses = [s for s in rec.get("legalStatuses") or [] if is_current(s, now)]
    if statuses:
        out["legal_status"] = [code_value(s.get("value")) for s in _by_start(statuses)]

    names = _by_start(rec.get("fullNames") or [])
    if len(names) > 1:
        out["former_names"] = [{"name": n.get("value"), **_timed(n)} for n in names if not is_current(n, on)]
    if rec.get("alternativeNames"):
        out["alternative_names"] = [n.get("value") for n in rec["alternativeNames"] if include_history or is_current(n, on)]

    bodies = rec.get("statutoryBodies") or []
    out["statutory_bodies"], out["former_statutory_bodies"] = _split([party(r) for r in bodies], bodies, on, include_history)
    authorization = current_value(rec.get("authorizations"), on)
    if authorization and is_current(authorization, on):
        out["acting"] = authorization.get("value")
    stakeholders = rec.get("stakeholders") or []
    out["stakeholders"], out["former_stakeholders"] = _split([party(r) for r in stakeholders], stakeholders, on, include_history)

    for key, fn in (("equity", equity), ("deposits", deposit), ("shares", share)):
        recs = rec.get({"equity": "equities"}.get(key, key)) or []
        items, _ = _split([fn(r) for r in recs], recs, on, include_history)
        if items:
            out[key] = items

    for key in ("predecessors", "successors"):
        if rec.get(key):
            out[key] = [related(r) for r in rec[key]]

    stats = rec.get("statisticalCodes") or {}
    if stats:
        out["main_activity"] = code_value(stats.get("mainActivity"))
        out["sector_esa2010"] = code_value(stats.get("esa2010"))
    activities = [a for a in rec.get("activities") or [] if is_current(a, on)]
    if activities:
        out["activities"] = [a.get("economicActivityDescription") for a in activities[:MAX_ACTIVITIES]]
        if len(activities) > MAX_ACTIVITIES:
            out["activities_not_shown"] = len(activities) - MAX_ACTIVITIES

    if include_history:
        addresses = _by_start(rec.get("addresses") or [])
        out["address_history"] = [{"address": address(a), **_timed(a)} for a in addresses]
        out["other_legal_facts"] = [{"fact": f.get("value"), **_timed(f)} for f in _by_start(rec.get("otherLegalFacts") or [])]
    out["data_updated"] = rec.get("dbModificationDate")
    return out
