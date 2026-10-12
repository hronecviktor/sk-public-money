"""Tools for the Slovak Central Register of Contracts (Centrálny register zmlúv, crz.gov.sk)."""

from __future__ import annotations

import time
from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from ...app import READ_ONLY, mcp, user_errors
from . import export, parse, pdf
from .client import CRZClient, check_url

SEARCH_PATH = "/2171273-sk/centralny-register-zmluv/"
SORT_ORDERS = {"newest": None, "amount_desc": "22", "amount_asc": "21"}
DEPARTMENTS_TTL = 24 * 3600

_client: CRZClient | None = None
_departments: tuple[float, list[dict]] | None = None


def client() -> CRZClient:
    global _client
    if _client is None:
        _client = CRZClient()
    return _client


def _sk_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%d.%m.%Y")


def _amount(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def crz_search_contracts(
    query: Annotated[str | None, Field(description="Full-text search over the register. Cannot be combined with the other filters (the portal ignores them).")] = None,
    supplier: Annotated[str | None, Field(description="Supplier (dodávateľ) name, substring match.")] = None,
    supplier_ico: Annotated[str | None, Field(description="Supplier IČO (company ID, up to 8 digits).")] = None,
    buyer: Annotated[str | None, Field(description="Buyer (objednávateľ, the public body) name, substring match.")] = None,
    buyer_ico: Annotated[str | None, Field(description="Buyer IČO.")] = None,
    title: Annotated[str | None, Field(description="Contract title (názov zmluvy), substring match.")] = None,
    contract_number: Annotated[str | None, Field(description="Contract number as assigned by the parties (číslo zmluvy).")] = None,
    department_id: Annotated[int | None, Field(description="Department (rezort) ID from crz_list_departments.")] = None,
    amount_from: Annotated[float | None, Field(description="Minimum total amount in EUR incl. VAT.")] = None,
    amount_to: Annotated[float | None, Field(description="Maximum total amount in EUR incl. VAT.")] = None,
    published_from: Annotated[str | None, Field(description="Published on or after this date, YYYY-MM-DD.")] = None,
    published_to: Annotated[str | None, Field(description="Published on or before this date, YYYY-MM-DD.")] = None,
    sort: Annotated[Literal["newest", "amount_desc", "amount_asc"], Field(description="Result order.")] = "newest",
    page: Annotated[int, Field(ge=0, description="0-based result page; 20 results per page.")] = 0,
) -> dict:
    """Search the Central Register of Contracts. Returns up to 20 contracts per page, each with its
    CRZ ID, publication date, title, contract number, total amount (EUR), supplier and buyer.
    Use crz_get_contract with an ID for full details and attachments."""
    filters = {
        "art_zs2": supplier,
        "art_ico": supplier_ico,
        "art_zs1": buyer,
        "art_ico1": buyer_ico,
        "art_predmet": title,
        "nazov": contract_number,
        "art_rezort": department_id,
        "art_suma_spolu_od": _amount(amount_from) if amount_from is not None else None,
        "art_suma_spolu_do": _amount(amount_to) if amount_to is not None else None,
        "art_datum_zverejnene_od": _sk_date(published_from) if published_from else None,
        "art_datum_zverejnene_do": _sk_date(published_to) if published_to else None,
    }
    filters = {k: v for k, v in filters.items() if v not in (None, "")}
    if query and filters:
        raise ValueError("The full-text query cannot be combined with other filters; use one or the other.")
    if not query and not filters:
        raise ValueError("Give a full-text query or at least one filter.")

    params: dict = {"search": query} if query else dict(filters)
    if SORT_ORDERS[sort]:
        params["order"] = SORT_ORDERS[sort]
    if page:
        params["page"] = page

    html, url = await client().get_html(SEARCH_PATH, params)
    result = parse.parse_search(html)
    result["search_url"] = url
    return result


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def crz_get_contract(
    contract_id: Annotated[int, Field(description="CRZ ID of a contract or amendment (as returned by crz_search_contracts, crz_daily_changes or the amendments list).")],
) -> dict:
    """Full detail of one contract or amendment: parties with addresses and IČO, department, dates
    (published, concluded/signed, effective, valid until), amounts in EUR, attachment links, and for a
    contract the list of its amendments (dodatky) with their IDs."""
    # index.php?ID= resolves both contracts (/zmluva/<id>/) and amendments (/<id>-sk/<slug>/).
    html, url = await client().get_html("/index.php", {"ID": contract_id})
    try:
        return parse.parse_contract(html, url=url)
    except parse.NotFound:
        raise ValueError(f"No contract or amendment with ID {contract_id} on crz.gov.sk.") from None


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def crz_read_attachment(
    url: Annotated[str, Field(description="Attachment URL from crz_get_contract or crz_daily_changes (https://www.crz.gov.sk/data/att/...).")],
    start_page: Annotated[int, Field(ge=1, description="First page to return (1-based).")] = 1,
    max_pages: Annotated[int, Field(ge=1, le=200, description="Maximum number of pages to return.")] = 20,
    max_chars: Annotated[int, Field(ge=1000, le=200_000, description="Maximum characters of text to return.")] = 40_000,
) -> dict:
    """Extract the text of a PDF attachment of a contract, page by page. Scanned documents usually
    have no text layer; the result then carries a note saying so."""
    full = check_url(url)
    if "/data/att/" not in full:
        raise ValueError("Only attachment URLs under https://www.crz.gov.sk/data/att/ can be read.")
    name = full.rsplit("/", 1)[-1]
    data = await client().get_bytes(full, cache_key=f"crz/att/{name}")
    result = pdf.extract_text(data, start_page=start_page, max_pages=max_pages, max_chars=max_chars)
    return {"url": full, **result}


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def crz_daily_changes(
    day: Annotated[str, Field(description="A past day, YYYY-MM-DD. The export for a day is built the following night around 02:00.")],
    text: Annotated[str | None, Field(description="Keep records whose number, title, description, buyer or supplier contains this text (case-insensitive).")] = None,
    ico: Annotated[str | None, Field(description="Keep records where the buyer or supplier has this IČO.")] = None,
    department_id: Annotated[int | None, Field(description="Keep records of this department (rezort) ID.")] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """All contract records published or changed on one day, from the official CRZ daily export
    (https://www.crz.gov.sk/export/YYYY-MM-DD.zip, CC BY 4.0). Includes old records that were edited
    that day; check dates.published to find new ones. Downloaded exports are cached locally."""
    data = await client().get_export(date.fromisoformat(day))
    records = export.parse_export_zip(data)
    matching = [r for r in records if export.matches(r, text=text, ico=ico, department_id=department_id)]
    return {
        "day": day,
        "total_records": len(records),
        "matching": len(matching),
        "offset": offset,
        "records": matching[offset : offset + limit],
        "has_more": offset + limit < len(matching),
    }


@mcp.tool(annotations=READ_ONLY)
@user_errors
async def crz_list_departments() -> dict:
    """Departments (rezorty: ministries, central bodies and other publishers) with their IDs,
    for the department_id filter."""
    global _departments
    if _departments is None or time.monotonic() - _departments[0] > DEPARTMENTS_TTL:
        html, _ = await client().get_html(SEARCH_PATH)
        _departments = (time.monotonic(), parse.parse_department_options(html))
    return {"departments": _departments[1]}

