"""Diacritics handling for Slovak, Czech and Hungarian names."""

from __future__ import annotations

import unicodedata

# Every lowercase letter a base letter can appear as in Slovak, Czech or Hungarian.
VARIANTS = {
    "a": "aáä",
    "c": "cč",
    "d": "dď",
    "e": "eéě",
    "i": "ií",
    "l": "lĺľ",
    "n": "nň",
    "o": "oóôöő",
    "r": "rŕř",
    "s": "sš",
    "t": "tť",
    "u": "uúůüű",
    "y": "yý",
    "z": "zž",
}


def fold(text: str) -> str:
    """Lowercase and strip diacritics: 'Široký' -> 'siroky'."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def letter_variants(ch: str) -> str:
    """All spellings of one character: 'š' -> 'sš', 'k' -> 'k'. The character itself is always included."""
    spellings = VARIANTS.get(fold(ch), "")
    return spellings if ch.lower() in spellings else spellings + ch.lower()
