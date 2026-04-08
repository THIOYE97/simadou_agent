"""
Normalize entity names for consistent storage and fuzzy matching.
"""
import re
import unicodedata
from typing import List


_STOP_WORDS = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "at",
    "al", "el", "ul", "bin", "bint",
}

_LEGAL_SUFFIXES_RE = re.compile(
    r"\b(llc|ltd|inc|corp|sa|sas|gmbh|bv|nv|pty|plc|co|company|corporation|"
    r"limited|incorporated|group|holding|holdings|international|enterprises?|"
    r"trading|services?|solutions?|technologies?|tech)\b",
    re.IGNORECASE,
)


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(name: str) -> str:
    """
    Returns a lowercase, accent-free, punctuation-light version of *name*.
    Used for the `name_normalized` column.
    """
    name = strip_accents(name)
    name = name.lower()
    # Remove punctuation except hyphens/apostrophes inside words
    name = re.sub(r"[^\w\s\-']", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def tokenize(normalized: str) -> List[str]:
    """
    Split a normalized name into meaningful tokens, dropping stop-words.
    Used for the `name_tokens` column (PostgreSQL ARRAY).
    """
    tokens = normalized.split()
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def detect_name_type(raw: str, tag_hint: str = "") -> str:
    """
    Heuristic to classify a name variant.
    tag_hint comes from the source XML attribute (e.g. "AKA", "MAIDEN_NAME").
    """
    hint = tag_hint.lower()
    if "maiden" in hint:
        return "maiden"
    if hint in ("aka", "a.k.a", "also known as", "alias"):
        return "aka"
    if hint in ("fka", "f.k.a", "formerly known as"):
        return "fka"
    if "low" in hint:
        return "low_quality"
    return "alias"
