"""Access to REPLIK, the register of insolvency, restructuring and liquidation proceedings
(Register predinsolvenčných, likvidačných a insolvenčných konaní, Ministry of Justice SR).

REPLIK replaced the Register úpadcov on 1 October 2025 and holds its migrated history. Its public
SOAP API needs no registration (integration manual "Integračná príručka – endpoint pre verejnosť",
version 1.0.12): konanieService (proceedings) and oznamService (public notices), both served from
the same endpoint. Pages start at 0 and hold at most 100 results. No rate limits are published, so
requests are spaced 1 s apart; notice lists take 2-8 s to answer.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from lxml import etree

from ...http import SourceUnavailable, ThrottledClient

ENDPOINT = "https://replik-ws.justice.sk/ru-verejnost-ws/"
NS_KONANIE = "datatypes.konanie.verejnost.ru.sk.hp.com"
NS_OZNAM = "datatypes.oznam.verejnost.ru.sk.hp.com"
SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
INTERVAL = 1.0
PAGE_MAX = 100

# The response is parsed with a hardened parser: no network, no entity expansion.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)


def to_dict(el: etree._Element):
    """An XML element as plain data: leaves become text (None when empty), elements with children
    become dicts, repeated children become lists. An xsi:type is kept under "_type"."""
    children = [c for c in el if isinstance(c.tag, str)]
    if not children:
        text = (el.text or "").strip()
        return text or None
    out: dict = {}
    if el.get(XSI_TYPE):
        out["_type"] = el.get(XSI_TYPE).split(":")[-1]
    for c in children:
        key = etree.QName(c).localname
        value = to_dict(c)
        if key in out:
            if not isinstance(out[key], list):
                out[key] = [out[key]]
            out[key].append(value)
        else:
            out[key] = value
    return out


def as_list(value) -> list:
    """A repeated element as a list, whether it occurred zero, one or more times."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class ReplikClient(ThrottledClient):
    def __init__(self, http: httpx.AsyncClient | None = None, cache: Path | None = None):
        super().__init__(lambda: INTERVAL, http=http, cache=cache)

    async def call(self, ns: str, operation: str, params: dict) -> dict:
        """Call <operation>Request in namespace ns and return its <operation>Response as data.

        params keep their order (the schema is a sequence); None values are left out.
        """
        envelope = etree.Element(f"{{{SOAP_ENV}}}Envelope", nsmap={"soapenv": SOAP_ENV, "d": ns})
        body = etree.SubElement(envelope, f"{{{SOAP_ENV}}}Body")
        request = etree.SubElement(body, f"{{{ns}}}{operation}Request")
        for key, value in params.items():
            if value is not None:
                etree.SubElement(request, f"{{{ns}}}{key}").text = str(value)
        payload = etree.tostring(envelope, xml_declaration=True, encoding="utf-8")

        resp = await self._request(
            "POST", ENDPOINT, content=payload,
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
        )
        try:
            root = etree.fromstring(resp.content, _PARSER)
        except etree.XMLSyntaxError:
            raise SourceUnavailable(
                f"replik-ws.justice.sk returned HTTP {resp.status_code} without a SOAP answer; try again later."
            ) from None
        fault = root.find(f".//{{{SOAP_ENV}}}Fault")
        if fault is not None:
            message = (fault.findtext("faultstring") or "").strip()
            # REPLIK reports bad input as a server fault too ("'abc' is not number!").
            if "not number" in message or "invalid" in message.lower():
                raise ValueError(f"REPLIK rejected the query: {message}")
            raise SourceUnavailable(f"REPLIK failed to answer ({message or 'SOAP fault'}); try again later.")
        if resp.status_code >= 400:
            raise SourceUnavailable(f"replik-ws.justice.sk returned HTTP {resp.status_code}; try again later.")
        response = root.find(f".//{{{ns}}}{operation}Response")
        if response is None:
            raise SourceUnavailable(f"REPLIK sent an unexpected answer to {operation}.")
        data = to_dict(response)
        return data if isinstance(data, dict) else {}

    # Proceedings (konania)

    async def proceedings_by_ico(self, ico: str, page: int, size: int) -> tuple[list[dict], int]:
        data = await self.call(NS_KONANIE, "getKonaniePodlaICO", {"Ico": ico, "Stranka": page, "VysledkovNaStranku": size})
        return _info_list(data, "KonanieInfoList", "KonanieInfo")

    async def search_proceedings(self, query: str, page: int, size: int) -> tuple[list[dict], int]:
        data = await self.call(NS_KONANIE, "vyhladajKonanie", {"Query": query, "Stranka": page, "VysledkovNaStranku": size})
        return _info_list(data, "KonanieInfoList", "KonanieInfo")

    async def proceeding(self, proceeding_id: int) -> dict | None:
        data = await self.call(NS_KONANIE, "getKonanieDetail", {"KonanieId": proceeding_id})
        found = as_list(data.get("Konanie"))
        return found[0] if found else None

    # Public notices (verejné oznamy)

    async def notices_by_file(self, file_number: str, court_code: str, page: int, size: int) -> tuple[list[dict], int]:
        data = await self.call(NS_OZNAM, "getVerejneOznamyPodlaZnackyASudu", {
            "SpisovaZnackaSudnehoSpisu": file_number, "SudKod": court_code, "Stranka": page, "VysledkovNaStranku": size,
        })
        return _info_list(data, "VerejnyOznamInfoList", "VerejnyOznamInfo")

    async def notices_by_ico(self, ico: str, page: int, size: int) -> tuple[list[dict], int]:
        data = await self.call(NS_OZNAM, "getVerejneOznamyPodlaICO", {"Ico": ico, "Stranka": page, "VysledkovNaStranku": size})
        return _info_list(data, "VerejnyOznamInfoList", "VerejnyOznamInfo")

    async def notice(self, notice_id: int) -> dict | None:
        data = await self.call(NS_OZNAM, "getVerejnyOznamDetail", {"OznamId": notice_id})
        return data.get("VerejnyOznam") if isinstance(data.get("VerejnyOznam"), dict) else None


def _info_list(data: dict, list_key: str, item_key: str) -> tuple[list[dict], int]:
    container = data.get(list_key)
    items = as_list(container.get(item_key)) if isinstance(container, dict) else []
    try:
        total = int(data.get("VysledkovCelkom") or 0)
    except ValueError:
        total = len(items)
    return items, total
