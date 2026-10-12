"""TED search results and notice XML -> compact readable dicts.

Notices come in two XML formats: eForms UBL (every notice since late 2023, with business terms
BT-xxx) and the older TED R2.0.9 schema (forms F01-F25). Both map to the same notice shape: buyers,
lots, and per lot its result with the tenders, their tenderers and the signed contracts.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .client import notice_url

# Lot result status (BT-142).
RESULT_STATUS = {"selec-w": "winner chosen", "clos-nw": "closed without a winner", "open-nw": "no winner yet"}
# Received submission types (BT-760) -> output keys.
SUBMISSION_TYPES = {
    "tenders": "tenders",
    "part-req": "participation_requests",
    "t-esubm": "electronic_tenders",
    "t-sme": "sme_tenders",
    "t-oth-eea": "tenders_other_eea",
    "t-no-eea": "tenders_non_eea",
    "t-verif-inad": "inadmissible_tenders",
    "t-verif-inad-low": "abnormally_low_tenders",
    "t-no-verif": "unverified_tenders",
}
# Procedure types of the old forms, mapped to the eForms codes (BT-105).
LEGACY_PROCEDURES = {
    "PT_OPEN": "open",
    "PT_RESTRICTED": "restricted",
    "PT_COMPETITIVE_NEGOTIATION": "neg-w-call",
    "PT_NEGOTIATED_WITH_PRIOR_CALL": "neg-w-call",
    "PT_COMPETITIVE_DIALOGUE": "comp-dial",
    "PT_INNOVATION_PARTNERSHIP": "innovation",
    "PT_AWARD_CONTRACT_WITHOUT_CALL": "neg-wo-call",
    "PT_NEGOTIATED_WITHOUT_PUBLICATION": "neg-wo-call",
}
FRAMEWORK_NOTE = "Values of framework agreements (rámcová dohoda) are the most the buyer may spend under them, not what was actually paid."
ZERO_NOTE = "A value of 0 means the buyer did not disclose it."
MAX_DESCRIPTION = 1500
CRZ_URL = re.compile(r"crz\.gov\.sk/(?:zmluva/|index\.php\?ID=|)(\d{4,})")


# --- identifiers -------------------------------------------------------------------------------

def classify_id(raw: str, country: str | None = None) -> dict:
    """Tell an IČO from a DIČ, IČ DPH or foreign registry number.

    Notices carry whatever the buyer typed into "national registration number": usually the IČO,
    often also the DIČ, for foreign firms their own registry or VAT number. country (ISO alpha-3) is
    the organisation's, when known: an 8-digit number of a Czech firm is a Czech IČO, not a Slovak one.
    """
    value = re.sub(r"[\s/.-]", "", raw or "").upper()
    if country and country not in ("SVK", "SK"):
        return {"id": value, "kind": "foreign", "country": country}
    if re.fullmatch(r"\d{6,8}", value):
        out = {"id": value.zfill(8), "kind": "ico"}
        if len(value) < 8:
            out["note"] = f"given as {value}, zero-padded"
        return out
    if re.fullmatch(r"[12]\d{9}", value):
        return {"id": value, "kind": "dic"}
    if re.fullmatch(r"SK\d{10}", value):
        return {"id": value, "kind": "ic_dph"}
    if re.fullmatch(r"[A-Z]{2}[0-9A-Z]{4,}", value):
        return {"id": value, "kind": "foreign", "country": value[:2]}
    return {"id": value, "kind": "unknown"}


def party(name: str | None, raw_ids: list[str], country: str | None = None, **extra) -> dict:
    """An organisation with its IČO picked out of its identifiers; ico is null when none is one."""
    ids = [classify_id(i, country) for i in raw_ids if i and i.strip()]
    ids = [i for n, i in enumerate(ids) if i["id"] not in {j["id"] for j in ids[:n]}]
    ico = next((i["id"] for i in ids if i["kind"] == "ico"), None)
    out = {"name": name, "ico": ico, "country": country}
    other = [i for i in ids if i["id"] != ico]
    if other:
        out["other_ids"] = other
    if ico is None:
        out["ico_note"] = "No Slovak IČO in the notice" + (f"; identifiers: {', '.join(i['id'] for i in ids)}" if ids else "")
    out.update(extra)
    return _prune(out)


def crz_id(url: str | None) -> int | None:
    """The CRZ contract ID in a link to crz.gov.sk, for crz_get_contract."""
    m = CRZ_URL.search(url or "")
    return int(m.group(1)) if m else None


def money(amount, currency: str | None = "EUR") -> dict | None:
    if amount in (None, ""):
        return None
    try:
        value = float(str(amount).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        return None
    return {"amount": int(value) if value.is_integer() else value, "currency": currency or "EUR"}


def _date(value: str | None) -> str | None:
    """'2026-10-05+02:00' or '2026-08-11T00:00:00' -> '2026-10-05'."""
    return value[:10] if value else None


# --- search results ----------------------------------------------------------------------------

SEARCH_FIELDS = [
    "publication-number", "publication-date", "notice-type", "form-type", "procedure-type",
    "title-proc", "notice-title", "buyer-name", "buyer-identifier", "winner-name", "winner-identifier",
    "winner-country", "classification-cpv", "total-value", "total-value-cur", "estimated-value-lot",
    "estimated-value-cur-lot", "BT-118-NoticeResult", "framework-maximum-value-lot", "BT-759-LotResult",
    "received-submissions-type-code", "contract-identifier", "notice-identifier",
]


def _lang(value, default=None):
    """TED gives texts per language ({'slk': ...}); prefer Slovak."""
    if isinstance(value, dict):
        for lang in ("slk", "eng"):
            if lang in value:
                return value[lang]
        return next(iter(value.values()), default)
    return value if value is not None else default


def _unique(values) -> list:
    return list(dict.fromkeys(v for v in values or [] if v not in (None, "")))


def _group_ids(names: list[str], raw_ids: list[str], countries: list[str] | None = None) -> list[dict]:
    """Pair organisation names with their identifiers from TED's flat lists.

    TED lists each organisation's identifiers in name order, IČO first, then maybe its DIČ or VAT
    number. A new group starts at each ID that is not a DIČ/VAT number; if the groups don't line up
    with the names, the IDs are returned unpaired rather than guessed.
    """
    groups: list[list[str]] = []
    for raw in raw_ids or []:
        kind = classify_id(raw)["kind"]
        if groups and kind in ("dic", "ic_dph"):
            groups[-1].append(raw)
        elif groups and kind == "foreign" and re.sub(r"\D", "", raw) == re.sub(r"\D", "", groups[-1][0]):
            groups[-1].append(raw)  # CZ27176100 after 27176100: the same Czech firm's VAT number
        else:
            groups.append([raw])
    if len(groups) == len(names):
        out = []
        for i, (name, ids) in enumerate(zip(names, groups)):
            country = countries[i] if countries and len(countries) == len(names) else None
            out.append(party(name, ids, country))
        return out
    parties = [{"name": n} for n in names]
    if raw_ids:
        parties.append({"unpaired_ids": [classify_id(i) for i in raw_ids],
                        "note": "TED's search result does not say which ID belongs to which name; uvo_get_notice does."})
    return parties


def _per_lot(values: list | None, codes: list | None, code: str) -> list[int] | None:
    """Pick one statistic per lot out of TED's flat parallel lists."""
    if not values or not codes or len(values) != len(codes):
        return None
    return [int(v) for v, c in zip(values, codes) if c == code and str(v).isdigit()]


def search_row(n: dict) -> dict:
    number = n["publication-number"]
    currency = _lang(n.get("total-value-cur"), ["EUR"])
    currency = currency[0] if isinstance(currency, list) and currency else "EUR"
    title = _lang(n.get("title-proc")) or _lang(n.get("notice-title"))
    estimated = [money(v, currency) for v in n.get("estimated-value-lot") or []]
    estimated_total = sum(e["amount"] for e in estimated if e) if any(estimated) else None
    framework = money(n.get("BT-118-NoticeResult"), currency) or (
        money(max(float(v) for v in n["framework-maximum-value-lot"]), currency) if n.get("framework-maximum-value-lot") else None
    )
    row = {
        "id": f"ted:{number}",
        "source": "ted",
        "published": _date(n.get("publication-date")),
        "notice_type": n.get("notice-type"),
        "procedure_type": n.get("procedure-type"),
        "title": title[0] if isinstance(title, list) else title,
        "buyers": _group_ids(_lang(n.get("buyer-name"), []), n.get("buyer-identifier") or []),
        "cpv": _unique(n.get("classification-cpv")),
        "estimated_value": money(estimated_total, currency) if estimated_total is not None else None,
        "total_value": money(n.get("total-value"), currency),
        "framework_maximum": framework,
        "tenders_received": _per_lot(n.get("BT-759-LotResult"), n.get("received-submissions-type-code"), "tenders"),
        "contract_numbers": _unique(n.get("contract-identifier")),
        "notice_uuid": n.get("notice-identifier"),
        "url": notice_url(number),
    }
    winner_names = _lang(n.get("winner-name"), [])
    if winner_names or n.get("winner-identifier"):
        row["winners"] = _group_ids(winner_names, n.get("winner-identifier") or [], n.get("winner-country"))
    notes = []
    if framework:
        notes.append(FRAMEWORK_NOTE)
    if row["total_value"] and row["total_value"]["amount"] == 0:
        notes.append(ZERO_NOTE)
    if notes:
        row["notes"] = notes
    return {k: v for k, v in row.items() if v not in (None, [], "")}


# --- notice XML --------------------------------------------------------------------------------

def _strip_ns(root: ET.Element) -> ET.Element:
    """Drop XML namespaces: element names are unique enough in both formats, and the old schema
    changes its namespace with every release (R2.0.8, R2.0.9)."""
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        for key in [k for k in el.attrib if "}" in k]:
            el.attrib[key.split("}", 1)[1]] = el.attrib.pop(key)
    return root


def _text(el: ET.Element | None, path: str | None = None) -> str | None:
    if el is None:
        return None
    found = el.find(path) if path else el
    if found is None:
        return None
    text = "".join(found.itertext()).strip()
    return re.sub(r"\s+", " ", text) or None


def _texts(el: ET.Element, path: str) -> list[str]:
    return [t for t in (_text(x) for x in el.findall(path)) if t]


def _amount(el: ET.Element | None, path: str) -> dict | None:
    found = el.find(path) if el is not None else None
    if found is None:
        return None
    return money(found.text, found.get("currencyID") or found.get("CURRENCY"))


def parse_notice(xml: bytes, number: str) -> dict:
    root = _strip_ns(ET.fromstring(xml))
    notice = _legacy(root) if root.tag == "TED_EXPORT" else _eforms(root)
    notice = {"id": f"ted:{number}", "source": "ted", "url": notice_url(number), **notice}
    notes = notice.setdefault("notes", [])
    if notice.get("framework_agreement"):
        notes.append(FRAMEWORK_NOTE)
    if notice.get("total_value") and notice["total_value"]["amount"] == 0:
        notes.append(ZERO_NOTE)
    if not notes:
        del notice["notes"]
    if len(notice.get("description") or "") > MAX_DESCRIPTION:
        notice["description"] = notice["description"][:MAX_DESCRIPTION].rsplit(" ", 1)[0] + " …"
    return _prune(notice)


def _prune(value):
    """Drop empty values at every level, keeping key order. ico stays, null saying "no IČO given"."""
    if isinstance(value, dict):
        pruned = ((k, _prune(v)) for k, v in value.items())
        return {k: v for k, v in pruned if k == "ico" or v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


# eForms

def _eforms_org(company: ET.Element) -> tuple[str, dict]:
    org_id = _text(company, "PartyIdentification/ID")
    address = company.find("PostalAddress")
    country = _text(address, "Country/IdentificationCode")
    street = " ".join(filter(None, [_text(address, "StreetName"), _text(address, "AdditionalStreetName")]))
    contact = company.find("Contact")
    return org_id, party(
        _text(company, "PartyName/Name"),
        _texts(company, "PartyLegalEntity/CompanyID"),
        country,
        address=", ".join(filter(None, [street, _text(address, "PostalZone"), _text(address, "CityName")])) or None,
        size=_text(company, "CompanySizeCode"),
        website=_text(company, "WebsiteURI"),
        contact={
            "name": _text(contact, "Name"),
            "email": _text(contact, "ElectronicMail"),
            "phone": _text(contact, "Telephone"),
        } if contact is not None else None,
    )


def _submissions(result: ET.Element) -> dict:
    out = {}
    for stat in result.findall("ReceivedSubmissionsStatistics"):
        code, count = _text(stat, "StatisticsCode"), _text(stat, "StatisticsNumeric")
        if code and count and count.isdigit() and (int(count) or code == "tenders"):
            out[SUBMISSION_TYPES.get(code, code)] = int(count)
    return out


def _eforms(root: ET.Element) -> dict:
    ext = root.find(".//EformsExtension")
    orgs = dict(_eforms_org(c) for c in ext.findall("Organizations/Organization/Company")) if ext is not None else {}
    result = ext.find("NoticeResult") if ext is not None else None
    publication = ext.find("Publication") if ext is not None else None

    parties: dict[str, dict] = {}
    tenders: dict[str, dict] = {}
    contracts: dict[str, dict] = {}
    lot_results: dict[str, list[ET.Element]] = {}
    if result is not None:
        for tp in result.findall("TenderingParty"):
            # Several tenderers in one party are a consortium (skupina dodávateľov).
            parties[_text(tp, "ID")] = {
                "tenderers": [orgs.get(_text(t, "ID"), {"org_ref": _text(t, "ID")}) for t in tp.findall("Tenderer")],
                "subcontractors": [orgs.get(_text(s, "ID"), {"org_ref": _text(s, "ID")}) for s in tp.findall("SubContractor")],
            }
        for t in result.findall("LotTender"):
            tenders[_text(t, "ID")] = {
                "id": _text(t, "ID"),
                "amount": _amount(t, "LegalMonetaryTotal/PayableAmount"),
                "rank": int(_text(t, "RankCode")) if (_text(t, "RankCode") or "").isdigit() else None,
                **parties.get(_text(t, "TenderingParty/ID"), {}),
                "lot": _text(t, "TenderLot/ID"),
            }
        for c in result.findall("SettledContract"):
            url = _text(c, "URI")
            contracts[_text(c, "ID")] = {
                "number": _text(c, "ContractReference/ID"),
                "title": _text(c, "Title"),
                "awarded": _date(_text(c, "AwardDate")),
                "concluded": _date(_text(c, "IssueDate")),
                "url": url,
                "crz_id": crz_id(url),
                "tenders": _texts(c, "LotTender/ID"),
            }
        for r in result.findall("LotResult"):
            lot_results.setdefault(_text(r, "TenderLot/ID"), []).append(r)

    project = root.find("ProcurementProject")
    lots = []
    framework = False
    for lot_el in root.findall("ProcurementProjectLot"):
        lot_id = _text(lot_el, "ID")
        lot_project = lot_el.find("ProcurementProject")
        lot_framework = lot_el.find(".//FrameworkMaximumAmount") is not None or _text(lot_el, ".//ContractingSystemTypeCode[@listName='framework-agreement']") not in (None, "none")
        framework = framework or lot_framework
        lot = {
            "id": lot_id,
            "title": _text(lot_project, "Name"),
            "cpv": _texts(lot_el, ".//MainCommodityClassification/ItemClassificationCode"),
            "estimated_value": _amount(lot_project, "RequestedTenderTotal/EstimatedOverallContractAmount"),
            "framework_maximum": _amount(lot_el, ".//FrameworkMaximumAmount"),
        }
        results = []
        for r in lot_results.get(lot_id, []):
            winning = _texts(r, "LotTender/ID")
            lot_tenders = [{**tenders[t], "winner": t in winning} for t in tenders if tenders[t]["lot"] == lot_id]
            for t in lot_tenders:
                t.pop("lot", None)
            framework_values = _amount(r, "FrameworkAgreementValues/MaximumValueAmount")
            framework = framework or framework_values is not None
            results.append({
                "status": RESULT_STATUS.get(_text(r, "TenderResultCode"), _text(r, "TenderResultCode")),
                "no_winner_reason": _text(r, "DecisionReason/DecisionReasonCode"),
                "bids": _submissions(r),
                "lowest_bid": _amount(r, "LowerTenderAmount"),
                "highest_bid": _amount(r, "HigherTenderAmount"),
                "framework_maximum": framework_values,
                "tenders": sorted(lot_tenders, key=lambda t: (not t["winner"], t["rank"] or 99)),
                "contracts": [contracts[c] for c in _texts(r, "SettledContract/ID") if c in contracts],
            })
        if results:
            lot["results"] = results
        lots.append(lot)

    buyer_refs = [_text(cp, "Party/PartyIdentification/ID") for cp in root.findall("ContractingParty")]
    notice_type = root.find("NoticeTypeCode")
    return {
        "published": _date(_text(publication, "PublicationDate")),
        "notice_type": _text(notice_type),
        "form_type": notice_type.get("listName") if notice_type is not None else None,
        "notice_uuid": _text(root, "ID[@schemeName='notice-id']"),
        "procedure_uuid": _text(root, "ContractFolderID"),
        "internal_id": _text(project, "ID"),
        "title": _text(project, "Name"),
        "description": _text(project, "Description"),
        "procedure_type": _text(root, "TenderingProcess/ProcedureCode"),
        "contract_nature": _text(project, "ProcurementTypeCode[@listName='contract-nature']"),
        "cpv": _unique(_texts(project, "MainCommodityClassification/ItemClassificationCode")
                       + _texts(project, "AdditionalCommodityClassification/ItemClassificationCode")),
        "buyers": [{**orgs.get(ref, {"org_ref": ref}), "profile_url": _text(cp, "BuyerProfileURI")}
                   for ref, cp in zip(buyer_refs, root.findall("ContractingParty"))],
        "estimated_value": _amount(project, "RequestedTenderTotal/EstimatedOverallContractAmount"),
        "total_value": _amount(result, "TotalAmount"),
        "framework_maximum": _amount(result, "OverallMaximumFrameworkContractsAmount"),
        "framework_agreement": framework or _amount(result, "OverallMaximumFrameworkContractsAmount") is not None,
        "lots": lots,
    }


# TED R2.0.9 (before eForms)

def _legacy_org(address: ET.Element | None, *, sme: bool | None = None) -> dict | None:
    if address is None:
        return None
    country = (address.find("COUNTRY").get("VALUE") if address.find("COUNTRY") is not None else None)
    country3 = {"SK": "SVK"}.get(country, country)
    return party(
        _text(address, "OFFICIALNAME"),
        _texts(address, "NATIONALID"),
        country3,
        address=", ".join(filter(None, [_text(address, "ADDRESS"), _text(address, "POSTAL_CODE"), _text(address, "TOWN")])) or None,
        size="sme" if sme else None,
        website=_text(address, "URL_GENERAL") or _text(address, "URL"),
        contact={"name": _text(address, "CONTACT_POINT"), "email": _text(address, "E_MAIL"), "phone": _text(address, "PHONE")},
    )


def _legacy_form(root: ET.Element) -> ET.Element | None:
    section = root.find("FORM_SECTION")
    forms = list(section) if section is not None else []
    return next((f for f in forms if f.get("LG") == "SK"), forms[0] if forms else None)


def _legacy(root: ET.Element) -> dict:
    coded = root.find("CODED_DATA_SECTION")
    form = _legacy_form(root)
    if form is None:
        raise ValueError("The notice has no form section TED could provide.")
    body = form.find("CONTRACTING_BODY")
    obj = form.find("OBJECT_CONTRACT")
    procedure = form.find("PROCEDURE")
    pt = next((c.tag for c in (procedure if procedure is not None else []) if c.tag.startswith("PT_")), None)

    buyers = [_legacy_org(body.find("ADDRESS_CONTRACTING_BODY"))] if body is not None else []
    if body is not None:
        buyers += [_legacy_org(a) for a in body.findall("ADDRESS_CONTRACTING_BODY_ADDITIONAL")]

    lots = []
    for award in form.findall("AWARD_CONTRACT"):
        awarded = award.find("AWARDED_CONTRACT")
        lot = {"id": _text(award, "LOT_NO") or award.get("ITEM"), "title": _text(award, "TITLE")}
        if awarded is None:
            no_award = award.find("NO_AWARDED_CONTRACT")
            reason = next(iter(no_award), None) if no_award is not None else None  # <PROCUREMENT_UNSUCCESSFUL/>
            lot["results"] = [{"status": "closed without a winner", "no_winner_reason": reason.tag.lower() if reason is not None else None}]
        else:
            tenders = awarded.find("TENDERS")
            bids = {}
            for tag, key in (("NB_TENDERS_RECEIVED", "tenders"), ("NB_TENDERS_RECEIVED_SME", "sme_tenders"),
                             ("NB_TENDERS_RECEIVED_EMEANS", "electronic_tenders"), ("NB_TENDERS_RECEIVED_OTHER_EU", "tenders_other_eea"),
                             ("NB_TENDERS_RECEIVED_NON_EU", "tenders_non_eea")):
                value = _text(tenders, tag)
                if value and value.isdigit() and (int(value) or key == "tenders"):
                    bids[key] = int(value)
            contractors = [_legacy_org(c.find("ADDRESS_CONTRACTOR"), sme=c.find("SME") is not None)
                           for c in awarded.findall("CONTRACTORS/CONTRACTOR")]
            values = awarded.find("VALUES")
            lot["estimated_value"] = _amount(values, "VAL_ESTIMATED_TOTAL")
            lot["results"] = [{
                "status": "winner chosen",
                "bids": bids,
                "lowest_bid": _amount(values, "VAL_RANGE_TOTAL/LOW"),
                "highest_bid": _amount(values, "VAL_RANGE_TOTAL/HIGH"),
                "tenders": [{"amount": _amount(values, "VAL_TOTAL"), "winner": True, "tenderers": contractors}],
                "contracts": [{"number": _text(award, "CONTRACT_NO"), "title": _text(award, "TITLE"),
                               "concluded": _text(awarded, "DATE_CONCLUSION_CONTRACT")}],
            }]
        lots.append(lot)

    document_type = coded.find(".//TD_DOCUMENT_TYPE") if coded is not None else None
    published = _text(coded, "REF_OJS/DATE_PUB") or ""  # 20220103
    return {
        "published": f"{published[:4]}-{published[4:6]}-{published[6:8]}" if re.fullmatch(r"\d{8}", published) else None,
        "notice_type": _text(document_type),
        "form_type": form.get("FORM"),
        "internal_id": _text(obj, "REFERENCE_NUMBER"),
        "title": _text(obj, "TITLE"),
        "description": _text(obj, "SHORT_DESCR"),
        "procedure_type": LEGACY_PROCEDURES.get(pt, pt.lower() if pt else None),
        "contract_nature": (obj.find("TYPE_CONTRACT").get("CTYPE") or "").lower() if obj is not None and obj.find("TYPE_CONTRACT") is not None else None,
        "cpv": _unique([c.get("CODE") for c in obj.iter("CPV_CODE")]) if obj is not None else [],
        "buyers": [b for b in buyers if b],
        "estimated_value": _amount(obj, "VAL_ESTIMATED_TOTAL"),
        "total_value": _amount(obj, "VAL_TOTAL"),
        "framework_agreement": procedure is not None and procedure.find("FRAMEWORK") is not None,
        "lots": lots,
        "format_note": "Published before eForms (TED schema R2.0.9): tenders other than the winners and contract links are not part of these notices.",
    }
