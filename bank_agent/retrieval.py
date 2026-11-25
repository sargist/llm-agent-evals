"""Keyword retrieval (BM25) over the help-center FAQ.

No embeddings on purpose: the FAQ is small, BM25 is deterministic and easy to test, and
the interesting failures for an agent (wrong query, query in the wrong language, citing a
document it never retrieved) show up just as well.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FAQ_DIR = Path(__file__).parent / "faq"

STOPWORDS = frozenset(
    """a an and are as at be by can do does for from how i if in is it its my of on or our so that the their
    there this to was we what when where which who why will with you your me much many""".split()  # noqa: SIM905
)
TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class FaqDoc:
    id: str
    title: str
    body: str
    keywords: tuple[str, ...] = field(default_factory=tuple)


def _stem(token: str) -> str:
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


def parse_faq(text: str) -> FaqDoc:
    """Parse a markdown file with a YAML front-matter block (id, title, keywords)."""
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text.replace("\r\n", "\n"), re.S)
    if not match:
        raise ValueError("FAQ document must start with a front-matter block")
    meta = yaml.safe_load(match.group(1))
    return FaqDoc(
        id=meta["id"],
        title=meta["title"],
        body=match.group(2).strip(),
        keywords=tuple(meta.get("keywords", ())),
    )


def load_faq_docs(directory: Path = FAQ_DIR) -> list[FaqDoc]:
    return [parse_faq(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.md"))]


class FaqIndex:
    """Okapi BM25. Title and keywords are indexed twice to weight them above body text."""

    def __init__(self, docs: list[FaqDoc], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = docs
        self.k1, self.b = k1, b
        self._tf: list[Counter[str]] = []
        for doc in docs:
            head = f"{doc.title} {' '.join(doc.keywords)}"
            self._tf.append(Counter(tokenize(head) * 2 + tokenize(doc.body)))
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg_len = sum(self._len) / len(self._len) if docs else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(docs)
        self._idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def search(self, query: str, top_k: int = 3) -> list[tuple[FaqDoc, float]]:
        terms = tokenize(query)
        scored = []
        for doc, tf, length in zip(self.docs, self._tf, self._len, strict=True):
            score = 0.0
            for term in terms:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                norm = (
                    freq * (self.k1 + 1) / (freq + self.k1 * (1 - self.b + self.b * length / self._avg_len))
                )
                score += self._idf[term] * norm
            if score > 0:
                scored.append((doc, round(score, 3)))
        scored.sort(key=lambda pair: (-pair[1], pair[0].id))
        return scored[:top_k]
