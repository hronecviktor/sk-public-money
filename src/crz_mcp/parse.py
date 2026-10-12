"""Parsers for crz.gov.sk HTML pages (search results and contract/amendment detail)."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

BASE_URL = "https://www.crz.gov.sk"

MONTHS = {
    "január": 1, "február": 2, "marec": 3, "apríl": 4, "máj": 5, "jún": 6,
    "júl": 7, "august": 8, "september": 9, "október": 10, "november": 11, "december": 12,
}

NOT_FOUND_TITLE = "Vybranú stránku nie je možné zobraziť"


class NotFound(Exception):
    pass


def _text(el: Tag | None) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _lines(el: Tag) -> list[str]:
    """Text of an element split on <br>, with empty lines dropped."""
    for br in el.find_all("br"):
        br.replace_with("\n")
    return [re.sub(r"\s+", " ", line).strip() for line in el.get_text().split("\n") if line.strip()]


def parse_amount(value: str) -> float | None:
    """'2 148,04 €' -> 2148.04"""
    cleaned = re.sub(r"[^\d,.-]", "", value.replace("\xa0", " ")).replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_sk_date(value: str) -> str | None:
    """'22.08.2017' -> '2017-08-22', '15.01.2024 14:32' -> '2024-01-15T14:32'; anything else -> None."""
    m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}:\d{2}))?\s*", value)
    if not m:
        return None
    d, mo, y, t = m.groups()
    iso = f"{y}-{int(mo):02d}-{int(d):02d}"
    return f"{iso}T{t}" if t else iso


def _cell_date(td: Tag) -> str | None:
    """Table date cell: <span>12.</span><span>Október</span><span>2026</span>."""
    parts = [s.get_text(strip=True) for s in td.find_all("span")]
    if len(parts) != 3:
        return None
    day, month, year = parts
    month_no = MONTHS.get(month.lower())
    if not month_no or not day.rstrip(".").isdigit() or not year.isdigit():
        return None
    return f"{year}-{month_no:02d}-{int(day.rstrip('.')):02d}"


def id_from_href(href: str) -> int | None:
    """'/zmluva/12920842/' or '/8818627-sk/dodatok-c1/' -> the numeric id."""
    m = re.match(r"^/(?:zmluva/)?(\d+)(?:-[a-z]{2})?/", urlparse(href).path)
    return int(m.group(1)) if m else None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _main(soup: BeautifulSoup) -> Tag:
    main = soup.find("main")
    if main is None:
        raise ValueError("Unexpected page layout: no <main> element")
    title = _text(main.find("h1"))
    if title == NOT_FOUND_TITLE:
        raise NotFound(title)
    return main


def _parse_list_rows(tbody: Tag | None) -> list[dict]:
    rows = []
    if tbody is None:
        return rows
    for tr in tbody.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 5:
            continue
        link = tds[1].find("a")
        href = link["href"] if link else ""
        number = tds[1].find("span")
        rows.append({
            "id": id_from_href(href),
            "url": urljoin(BASE_URL, href) if href else None,
            "published": _cell_date(tds[0]),
            "title": _text(link),
            "number": _text(number) or None,
            "amount_eur": parse_amount(_text(tds[2])),
            "supplier": _text(tds[3]) or None,
            "buyer": _text(tds[4]) or None,
        })
    return rows


def parse_search(html: str) -> dict:
    soup = _soup(html)
    main = _main(soup)
    table = main.find("table", class_="table_list")
    results = _parse_list_rows(table.find("tbody") if table else None)

    page = 0
    has_more = False
    pagination = main.find("ul", class_="pagination")
    if pagination:
        current = pagination.find("li", class_="active")
        if current:
            num = re.match(r"\d+", _text(current))
            page = int(num.group(0)) - 1 if num else 0
        nxt = pagination.find("a", class_="page-link---next")
        has_more = nxt is not None
    return {"page": page, "results": results, "has_more": has_more}


def parse_department_options(html: str) -> list[dict]:
    soup = _soup(html)
    select = soup.find("select", attrs={"name": "art_rezort"})
    if select is None:
        return []
    out = []
    for opt in select.find_all("option"):
        value = opt.get("value", "")
        name = _text(opt)
        if value.isdigit() and value != "0" and name:
            out.append({"id": int(value), "name": name})
    return out


_PARTY_LABELS = {"Objednávateľ": "buyer", "Obstarávateľ": "buyer", "Dodávateľ": "supplier"}


def _parse_attachments(card: Tag) -> list[dict]:
    out = []
    for li in card.find_all("li"):
        link = li.find("a", href=True)
        if not link:
            continue
        kind_el = li.find("span", class_="text-uppercase")
        size_el = li.find("small")
        size_text = _text(size_el).strip("()")
        fmt, _, size = (p.strip() for p in size_text.partition(","))
        out.append({
            "name": _text(link),
            # "text" = the published text version, "sken" = a scan
            "kind": _text(kind_el).lower() or None,
            "url": urljoin(BASE_URL, link["href"]),
            "format": fmt.lstrip(".") or None,
            "size": size or None,
        })
    return out


def parse_contract(html: str, url: str | None = None) -> dict:
    """Parse a contract (/zmluva/<id>/) or amendment (/<id>-sk/<slug>/) detail page."""
    soup = _soup(html)
    main = _main(soup)

    result: dict = {
        "id": None,
        "url": url,
        "kind": "contract",
        "title": _text(main.find("h1")),
        "buyer": {},
        "supplier": {},
        "dates": {},
        "amounts_eur": {},
        "attachments": [],
        "amendments": [],
        "fields": {},
    }

    fields: dict[str, str] = {}
    last_party: str | None = None
    for li in main.find_all("li"):
        label_el = li.find("strong")
        value_el = label_el.find_next_sibling("span") if label_el else None
        if not label_el or value_el is None:
            continue
        label = _text(label_el).rstrip(":").strip()
        lines = _lines(value_el)
        value = "\n".join(lines)
        if label in fields:
            # Repeated labels (IČO appears once per party): key them by the party they follow.
            label = f"{label} ({last_party})" if last_party else label
        fields[label] = value

        base_label = _text(label_el).rstrip(":").strip()
        if base_label in _PARTY_LABELS:
            last_party = _PARTY_LABELS[base_label]
            party = result[last_party]
            party["name"] = lines[0] if lines else None
            if len(lines) > 1:
                party["address"] = ", ".join(lines[1:])
        elif base_label == "IČO" and last_party:
            result[last_party]["ico"] = value or None

    result["fields"] = fields

    headers = [_text(card.find(class_="card-header")) for card in main.find_all("div", class_="card")]
    if not any(h.startswith("Identifikácia") for h in headers):
        # Unknown IDs redirect to other portal pages (e.g. the register's search page).
        raise NotFound(result["title"])

    for card in main.find_all("div", class_="card"):
        header = _text(card.find(class_="card-header"))
        if header == "Identifikácia dodatku":
            result["kind"] = "amendment"
        elif header in ("Príloha", "Prílohy"):
            result["attachments"] = _parse_attachments(card)
        elif header == "Cenové plnenie":
            body_text = _text(card)
            for key, label in (("agreed", "Zmluvne dohodnutá čiastka:"), ("total", "Celková čiastka:")):
                m = re.search(re.escape(label) + r"\s*([\d\s\xa0,.]+)\s*€", body_text)
                if m:
                    result["amounts_eur"][key] = parse_amount(m.group(1))

    get = fields.get
    if get("ID zmluvy", "").isdigit():
        result["id"] = int(get("ID zmluvy"))
    elif url:
        result["id"] = id_from_href(urlparse(url).path)

    result["type"] = get("Typ") or None
    result["number"] = get("Č. zmluvy") or get("Číslo dodatku") or None
    result["department"] = get("Rezort") or None
    result["subject"] = get("Názov zmluvy") or get("Názov dodatku") or None
    result["description"] = get("Popis predmetu zmluvy") or None
    result["note"] = get("Poznámka") or None
    result["status"] = get("Stav") or None
    result["published_by"] = get("Zverejnil") or None

    for key, label in (
        ("published", "Dátum zverejnenia"),
        ("concluded", "Dátum uzavretia"),
        ("signed", "Dátum podpisu"),
        ("effective", "Dátum účinnosti"),
        ("valid_until", "Dátum platnosti do"),
    ):
        if label in fields:
            result["dates"][key] = parse_sk_date(fields[label])
    if "Suma na zmluve" in fields:
        result["amounts_eur"]["amount"] = parse_amount(fields["Suma na zmluve"])

    heading = main.find(lambda t: t.name == "h2" and _text(t).startswith("Dodatky"))
    if heading:
        table = heading.find_next("table")
        if table:
            result["amendments"] = _parse_list_rows(table.find("tbody"))

    back = next(
        (a for a in main.find_all("a", href=re.compile(r"^/zmluva/\d+/")) if "zmluvu" in _text(a)),
        None,
    )
    if back is not None:
        result["parent_contract_id"] = id_from_href(back["href"])

    return result

