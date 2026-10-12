"""Text extraction from contract attachments."""

from __future__ import annotations

import io

from pypdf import PdfReader


def extract_text(data: bytes, *, start_page: int = 1, max_pages: int = 20, max_chars: int = 40_000) -> dict:
    if not data.startswith(b"%PDF"):
        raise ValueError("The attachment is not a PDF; only PDF text extraction is supported.")
    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    first = max(1, start_page)
    last = min(total, first + max_pages - 1)

    pages = []
    used = 0
    truncated = False
    for number in range(first, last + 1):
        text = (reader.pages[number - 1].extract_text() or "").strip()
        if used + len(text) > max_chars:
            text = text[: max_chars - used]
            truncated = True
        pages.append({"page": number, "text": text})
        used += len(text)
        if truncated:
            break

    result = {
        "pages_total": total,
        "pages_returned": [pages[0]["page"], pages[-1]["page"]] if pages else [],
        "truncated": truncated or bool(pages and pages[-1]["page"] < total),
        "pages": pages,
    }
    if pages and sum(len(p["text"]) for p in pages) < 20 * len(pages):
        result["note"] = "Little or no text layer: this is probably a scanned document (no OCR is done)."
    return result
