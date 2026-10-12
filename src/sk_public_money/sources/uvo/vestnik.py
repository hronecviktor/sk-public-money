"""Parser for the Vestník verejného obstarávania open data (data.slovensko.sk, CC BY 4.0 by ÚVO).

ÚVO publishes each issue of its journal as one JSON file. Every item is a notice serialised as the
tree of its web form: groups ("GR-...") hold fields keyed by eForms business terms (BT-xxx) for
eForms notices, or by Slovak field names for the half-year summary reports (súhrnná správa). Repeated
things (organisations, lots, tenders, contracts) are repeated "_panel" groups. A panel's own ID
(TEN-0001, TPA-0002, CON-0003) appears only in its Slovak label, so it is read from there, with the
panel's position as fallback. Each field also carries Slovak labels for its coded values, which are
used to spell out codes like "SS_IN".

Notices map to the same shape as TED notices (see parse.py); a summary report becomes one record per
procurement it lists.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from . import parse
from .parse import RESULT_STATUS, SUBMISSION_TYPES, crz_id, money, party

PANEL_ID = re.compile(r"\(([A-Z]{3}-\d{4})\)\s*$")
UVO_ID = re.compile(r"\(ID:\s*(\d+)\)\s*$")
SUMMARY_REPORT = "Súhrnná správa"
# Offers by type in a summary report -> the keys used for eForms statistics.
REPORT_SUBMISSIONS = {
    "ponuky": "tenders",
    "ponukyElektronicky": "electronic_tenders",
    "ponukyOdMsp": "sme_tenders",
    "ponukyZEu": "tenders_other_eea",
    "ponukyNieEu": "tenders_non_eea",
}


@dataclass
class Node:
    key: str | None
    value: str | list[str] | None = None  # some forms store references as lists: ['TEN-0001']
    label: str | None = None  # the Slovak label of a coded value, when it differs from the value
    own_id: str | None = None  # a panel's ID from its label: TEN-0001
    children: list[Node] = field(default_factory=list)

    def walk(self) -> Iterator[Node]:
        yield self
        for c in self.children:
            yield from c.walk()

    def groups(self, key: str) -> list[Node]:
        """Descendants with this key, not looking inside a match."""
        out = []
        for c in self.children:
            if c.key == key:
                out.append(c)
            else:
                out.extend(c.groups(key))
        return out

    def group(self, key: str) -> Node | None:
        found = self.groups(key)
        return found[0] if found else None

    def all(self, key: str) -> list[str]:
        out = []
        for n in self.walk():
            if n.key == key and n.value not in (None, ""):
                out.extend(n.value if isinstance(n.value, list) else [n.value])
        return out

    def get(self, key: str) -> str | None:
        found = self.all(key)
        return found[0] if found else None

    def label_of(self, key: str) -> str | None:
        """The Slovak label of a coded value, or the value itself."""
        for n in self.walk():
            if n.key == key and n.value not in (None, "", []):
                return n.label or (n.value[0] if isinstance(n.value, list) else n.value)
        return None

    def money(self, key: str) -> dict | None:
        """A '<key>_value' / '<key>_currency' pair: '333 520.00' + 'EUR'."""
        return money(self.get(f"{key}_value"), self.get(f"{key}_currency"))


def build(raw: dict) -> Node:
    sk = (raw.get("lang") or {}).get("sk") or {}
    value = raw.get("value")
    if isinstance(value, dict):  # some coded fields: {'value': '114', 'label': '§ 1 ods. 14'}
        value, label = value.get("value"), value.get("label")
    else:
        label = None
    if isinstance(value, list):
        value = [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()] or None
    elif isinstance(value, dict):
        value = None
    elif value is not None:
        value = str(value).strip()
        label = label or (sk.get(value) if sk.get(value) not in (None, value) else None)
    own = PANEL_ID.search(sk.get(raw.get("label") or "", "") or "")
    node = Node(raw.get("key"), value or None, label, own.group(1) if own else None)
    for k, v in raw.items():
        if k == "lang":
            continue
        for child in v if isinstance(v, list) else [v] if isinstance(v, dict) else []:
            if isinstance(child, dict):
                node.children.append(build(child))
    return node


def _ids(panels: list[Node], prefix: str) -> list[tuple[str, Node]]:
    return [(p.own_id or f"{prefix}-{i:04d}", p) for i, p in enumerate(panels, 1)]


def _uvo_ref(value: str | None) -> int | None:
    """'DNS na nákup ... (ID: 540073)' -> 540073."""
    m = UVO_ID.search(value or "")
    return int(m.group(1)) if m else None


def _org(panel: Node) -> dict:
    country = panel.get("BT-514-Organization-Company")
    street = " ".join(filter(None, [panel.get("BT-510(a)-Organization-Company"), panel.get("BT-510(b)-Organization-Company")]))
    ids = [n.value for n in panel.walk() if (n.key or "").startswith("BT-501-Organization-Company") and n.value]
    return party(
        panel.get("BT-500-Organization-Company"),
        ids,
        country,
        address=", ".join(filter(None, [street, panel.get("BT-512-Organization-Company"), panel.get("BT-513-Organization-Company")])) or None,
        size=panel.get("BT-165-Organization-Company"),
        website=panel.get("BT-505-Organization-Company"),
        contact={
            "name": panel.get("BT-502-Organization-Company"),
            "email": panel.get("BT-506-Organization-Company"),
            "phone": panel.get("BT-503-Organization-Company"),
        },
    )


def _submissions(result: Node) -> dict:
    out = {}
    for p in result.groups("GR-LotResult-ReceivedSubmissions_panel"):
        code, count = p.get("BT-760-LotResult"), p.get("BT-759-LotResult")
        if code and count and count.isdigit() and (int(count) or code == "tenders"):
            out[SUBMISSION_TYPES.get(code, code)] = int(count)
    return out


def _lot_contracts(contracts: dict, refs: list[str], winning: list[str], lot_tenders: set[str], used: set[str]) -> list[dict]:
    """The contracts a lot result names; failing that (older forms name none), those signed on its
    winning tenders, or else the first result of a lot gets those signed on any of its tenders."""
    if refs:
        chosen = [cid for cid in contracts if cid in refs]
    else:
        tenders = set(winning) or lot_tenders
        chosen = [cid for cid, c in contracts.items() if cid not in used and tenders & set(c["tenders"])]
    used.update(chosen)
    return [contracts[cid] for cid in chosen]


def eforms_notice(root: Node) -> dict:
    orgs = {oid: _org(p) for oid, p in _ids(root.groups("GR-Organisations_panel"), "ORG")
            for oid in [p.get("DL-Context-Org") or oid]}
    ref = lambda oid: orgs.get(oid, {"org_ref": oid})  # noqa: E731

    parties = {}
    for pid, p in _ids(root.groups("GR-TenderingParty_panel"), "TPA"):
        parties[pid] = {
            "tenderers": [ref(o) for o in p.all("OPT-300-Tenderer")],
            "subcontractors": [ref(o) for o in p.all("OPT-301-Tenderer-SubCont")],
        }
    tenders = {}
    for tid, p in _ids(root.groups("GR-LotTender_panel"), "TEN"):
        rank = p.get("BT-171-Tender")
        tenders[tid] = {
            "id": tid,
            "amount": p.money("BT-720-Tender"),
            "rank": int(rank) if rank and rank.isdigit() else None,
            **parties.get(p.get("OPT-310-Tender"), {}),
            "lot": p.get("BT-13714-Tender"),
        }
    contracts = {}
    for cid, p in _ids(root.groups("GR-SettledContract_panel"), "CON"):
        url = p.get("BT-151-Contract")
        contracts[cid] = {
            "number": p.get("BT-150-Contract"),
            "title": p.get("BT-721-Contract"),
            "awarded": parse._date(p.get("BT-1451-Contract")),
            "concluded": parse._date(p.get("BT-145-Contract")),
            "url": url,
            "crz_id": crz_id(url),
            "tenders": p.all("BT-3202-Contract"),
        }

    framework = False
    lots = []
    used: set[str] = set()
    results_by_lot: dict[str, list[Node]] = {}
    for r in root.groups("GR-LotResult_panel"):
        results_by_lot.setdefault(r.get("BT-13713-LotResult"), []).append(r)
    for lid, p in _ids(root.groups("GR-Lot_well"), "LOT"):
        lid = p.get("DL-Context-Lot") or lid
        lot_framework = (p.get("BT-765-Lot") or "none") != "none"
        framework = framework or lot_framework
        lot = {
            "id": lid,
            "title": p.get("BT-21-Lot") or p.get("DL-Title-Lot"),
            "cpv": p.all("BT-262-Lot"),
            "estimated_value": p.money("BT-27-Lot"),
            "framework_maximum": p.money("BT-271-Lot"),
        }
        results = []
        for r in results_by_lot.get(lid, []):
            winning = r.all("OPT-320-LotResult")
            lot_tenders = [{**{k: v for k, v in t.items() if k != "lot"}, "winner": tid in winning}
                           for tid, t in tenders.items() if t["lot"] == lid]
            maximum = r.money("BT-709-LotResult")
            framework = framework or maximum is not None
            results.append({
                "status": RESULT_STATUS.get(r.get("BT-142-LotResult"), r.get("BT-142-LotResult")),
                "no_winner_reason": r.get("BT-144-LotResult"),
                "bids": _submissions(r),
                "lowest_bid": r.money("BT-710-LotResult"),
                "highest_bid": r.money("BT-711-LotResult"),
                "framework_maximum": maximum,
                "tenders": sorted(lot_tenders, key=lambda t: (not t["winner"], t["rank"] or 99)),
                "contracts": _lot_contracts(contracts, r.all("OPT-315-LotResult"), winning, {t["id"] for t in lot_tenders}, used),
            })
        if results:
            lot["results"] = results
        lots.append(lot)

    procedure = root.group("GR-Procedure") or Node(None)
    buyers = [{**ref(p.get("OPT-300-Procedure-Buyer")), "profile_url": p.get("BT-508-Procedure-Buyer")}
              for p in root.groups("GR-ContractingAuthority_panel") if p.get("OPT-300-Procedure-Buyer")]
    total = root.money("BT-161-NoticeResult")
    maximum = root.money("BT-118-NoticeResult")
    return {
        "notice_type": root.get("BT-02-notice"),
        "form_type": root.get("BT-03-notice"),
        "notice_uuid": root.get("BT-701-notice"),
        "procedure_uuid": root.get("BT-04-notice"),
        "uvo_procurement_id": _uvo_ref(root.get("DL-Metadata-Order")),
        "internal_id": procedure.get("BT-22-Procedure"),
        "title": procedure.get("BT-21-Procedure"),
        "description": procedure.get("BT-24-Procedure"),
        "procedure_type": procedure.get("BT-105-Procedure"),
        "contract_nature": procedure.get("BT-23-Procedure"),
        "cpv": parse._unique(procedure.all("BT-262-Procedure") + procedure.all("BT-263-Procedure")),
        "buyers": buyers,
        "estimated_value": procedure.money("BT-27-Procedure"),
        "total_value": total,
        "framework_maximum": maximum,
        "framework_agreement": framework or maximum is not None,
        "lots": lots,
        "other_contracts": [c for cid, c in contracts.items() if cid not in used],
    }


def _report_party(n: Node, suffix: str = "") -> dict:
    g = lambda k: n.get(k + suffix)  # noqa: E731
    street = " ".join(filter(None, [g("ulica"), g("cislo")]))
    return party(
        g("uradnyNazov"),
        [i for i in (g("ico"), g("dic")) if i],
        g("stat"),
        address=", ".join(filter(None, [street, g("psc"), g("mestoObec")])) or None,
        size="sme" if g("dodavatelomJeMsp") == "True" else None,
        website=g("internetovaAdresaUrl"),
        contact={"name": g("kontaktnaOsoba"), "email": g("kontaktnaEmailovaAdresa"), "phone": g("kontaktneTelefonneCislo")},
    )


def report_records(root: Node) -> list[dict]:
    """A half-year summary report (súhrnná správa): one record per procurement it lists."""
    head = root.group("hlavickaFormulara") or Node(None)
    period = " ".join(filter(None, [head.get("rok"), {"1Polrok": "H1", "2Polrok": "H2"}.get(head.get("polrok") or "", head.get("polrok"))]))
    buyer_node = root.group("identifikacneUdajeVerejnehoObstaravatela")
    buyers = [_report_party(buyer_node)] if buyer_node else []
    nature = root.label_of("druhZakazky")
    records = []
    for n, z in enumerate(root.groups("zadanieZakazky"), 1):
        offers = z.group("prijatePonuky") or Node(None)
        bids = {}
        for p in [offers] + offers.groups("ponuky"):
            kind, count = p.get("typPrijatychPonuk"), p.get("pocetPrijatychPonuk")
            if kind and count and count.isdigit() and REPORT_SUBMISSIONS.get(kind) not in bids:
                bids[REPORT_SUBMISSIONS.get(kind, kind)] = int(count)
        contracts = []
        for c in z.groups("zmluva"):
            url = c.get("odkazNaZverejnenuZmluvu")
            contracts.append({
                "number": c.get("identifikatorZmluvy"),
                "title": c.get("nazovZmluvy"),
                "concluded": parse._date(c.get("datumUzavretiaZmluvy")),
                "value_excl_vat": money(c.get("hodnotaKtoraSaBralaDoUvahyBezDph"), c.get("mena")),
                "framework_agreement": c.get("ramcovaDohoda") == "True" or None,
                "exemption": c.label_of("vynimka"),
                "url": url,
                "crz_id": crz_id(url),
                "suppliers": [_report_party(s, "Dodavatel") for s in c.groups("identifikaciaDodavatela")],
            })
        records.append({
            "record": n,
            "kind": SUMMARY_REPORT,
            "report_type": head.label_of("vyberTypuSuhrnnejSpravy"),
            "period": period or None,
            "internal_id": z.get("externeIdZakazky"),
            "title": z.get("nazovZakazky"),
            "contract_nature": z.label_of("doplnujuciDruhZakazky") or nature,
            "buyers": buyers,
            "uvo_procurement_id": int(z.get("vyberZakazky")) if (z.get("vyberZakazky") or "").isdigit() else None,
            "lots": [{"results": [{"bids": bids, "contracts": contracts}]}],
            "framework_agreement": any(c["framework_agreement"] for c in contracts) or None,
        })
    return records


def parse_issue(data: bytes | str) -> tuple[dict, list[dict]]:
    """An issue -> ({year, number, published, skipped}, notices). Items that are not procurement
    notices (ÚVO's own supervision notices, sent as XML) are left out; items that fail to parse are
    listed in skipped with the error, so one odd form does not lose the whole issue."""
    issue = json.loads(data)
    year, number = int(issue["year"]), int(issue["number"])
    published = str(issue.get("bulletinPublishDate") or "")[:10] or None
    out, skipped = [], []
    for item in issue.get("bulletinItemList") or []:
        text = item.get("itemData") or ""
        if not text.lstrip().startswith("{"):
            continue
        try:
            raw = json.loads(text)
            root = build(raw)
            base = {"source": "vestnik", "issue": f"{number}/{year}", "published": published, "kind": (raw.get("name") or "").strip()}
            if base["kind"] == SUMMARY_REPORT:
                notices = [{"id": f"vestnik:{raw['id']}-{rec.pop('record')}", **base, **rec} for rec in report_records(root)]
            else:
                notices = [{"id": f"vestnik:{raw['id']}", **base, **eforms_notice(root)}]
            out.extend(finish(n) for n in notices)
        except Exception as exc:  # noqa: BLE001  (a form variant this parser does not know yet)
            skipped.append(f"item {item.get('submissionId')}: {type(exc).__name__}: {exc}")
    meta = {"year": year, "number": number, "published": published}
    if skipped:
        meta["skipped"] = skipped
    return meta, out


def finish(notice: dict) -> dict:
    """Notes on framework and undisclosed values, a capped description, and no empty fields."""
    notes = []
    if notice.get("framework_agreement"):
        notes.append(parse.FRAMEWORK_NOTE)
    if (notice.get("total_value") or {}).get("amount") == 0:
        notes.append(parse.ZERO_NOTE)
    notice["notes"] = notes
    description = notice.get("description") or ""
    if len(description) > parse.MAX_DESCRIPTION:
        notice["description"] = description[: parse.MAX_DESCRIPTION].rsplit(" ", 1)[0] + " …"
    return parse._prune(notice)
