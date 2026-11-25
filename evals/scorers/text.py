"""Language-aware text helpers: answer-language detection, number extraction, fact matching.

Numbers are written differently in English, Russian and Armenian answers (1,250.00 /
1 250,00 / 2,1%), so facts are compared as parsed numbers, not as strings.
"""

from __future__ import annotations

import re
from typing import Any

ARMENIAN_RE = re.compile(r"[Ա-Ֆՙ-՟ա-ևﬓ-ﬗ]")
CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
LATIN_RE = re.compile(r"[A-Za-zÀ-ɏ]")
# Tokens that are the same in every language: ids, URLs, codes, brand and product names.
NEUTRAL_RE = re.compile(
    r"\b(?:ACC|CARD|TX|TRF|faq)-[\w-]+|https?://\S+|\b[A-Z]{2,5}\b|Acme Bank|Acme|"
    r"Apple Pay|Google Pay|Exchange|Statements|Cards|Accounts"
)
MIN_SHARE = 0.6

NUMBER_RE = re.compile(
    r"(?<![\w.,])(?:"
    r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?"  # 1 250,00
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"  # 1,250.00
    r"|\d{1,3}(?:\.\d{3}){2,}(?:,\d+)?"  # 1.250.000,00
    r"|\d+(?:[.,]\d+)?"  # 1250.00 / 1250,00 / 2.1
    r")(?!\d)"
)
DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")
LIST_MARKER_RE = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+")


def detect_language(text: str) -> tuple[str, float]:
    """Dominant script of the text: ('hy' | 'ru' | 'en' | 'unknown', share of letters)."""
    body = NEUTRAL_RE.sub(" ", text)
    counts = {
        "hy": len(ARMENIAN_RE.findall(body)),
        "ru": len(CYRILLIC_RE.findall(body)),
        "en": len(LATIN_RE.findall(body)),
    }
    total = sum(counts.values())
    if total == 0:
        return "unknown", 0.0
    best = max(counts, key=lambda k: counts[k])
    return best, round(counts[best] / total, 3)


def language_matches(text: str, expected: str) -> bool:
    detected, share = detect_language(text)
    return detected == expected and share >= MIN_SHARE


def _to_float(token: str) -> float:
    t = re.sub(r"[   ]", "", token)
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        head, _, tail = t.rpartition(",")
        t = t.replace(",", "") if (t.count(",") > 1 or len(tail) == 3) else f"{head}.{tail}"
    elif t.count(".") > 1:
        t = t.replace(".", "")
    return float(t)


def extract_numbers(text: str, skip_list_markers: bool = False) -> list[float]:
    if skip_list_markers:
        text = LIST_MARKER_RE.sub(" ", text)
    numbers: list[float] = []
    for match in DATE_RE.finditer(text):
        numbers.extend(float(g) for g in match.groups())
    text = DATE_RE.sub(" ", text)
    numbers.extend(_to_float(m.group(0)) for m in NUMBER_RE.finditer(text))
    return numbers


def has_number(text: str, value: float) -> bool:
    return any(abs(n - value) < 0.005 for n in extract_numbers(text))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def fact_matches(answer: str, fact: Any) -> bool:
    """A fact is a substring, {"number": x}, {"any": [substrings]} or {"regex": pattern}."""
    if isinstance(fact, str):
        return _norm(fact) in _norm(answer)
    if "number" in fact:
        return has_number(answer, float(fact["number"]))
    if "any" in fact:
        return any(fact_matches(answer, option) for option in fact["any"])
    if "regex" in fact:
        return re.search(fact["regex"], answer, re.I) is not None
    raise ValueError(f"Unsupported fact spec: {fact!r}")
