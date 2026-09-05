"""
Description Parser — Bank Narration Counterparty Extraction
=============================================================
Real bank statement narration (NEFT/IMPS/UPI/FT/RTGS rails) embeds the
counterparty name inside a messy, rail-specific delimited string instead
of a clean column. This module extracts a best-effort counterparty name
and rail type using pure regex/heuristics — deterministic and auditable,
never an LLM guess. Extraction confidence is reported so callers can be
honest about uncertainty instead of presenting a guess as fact.

Precomputed once per row at ingestion time (see db/init_db.py) into the
`transaction_derived` table, so SQL generation queries a plain column
instead of re-deriving this per query.
"""
import re
from dataclasses import dataclass

# Bank codes from the fixed bank list — embedded in narration as noise,
# never the counterparty itself.
KNOWN_BANK_CODES = {
    "hdfc", "icic", "sbin", "utib", "kkbk",
    "cnrb", "ubin", "aubl", "tmbl", "ratn",
}

# Rail/jargon tokens that show up as standalone segments and must never
# be mistaken for a counterparty name.
RAIL_TOKENS = {
    "p2a", "inet", "ow", "neft", "imps", "upi", "ft", "rtgs", "inwd", "r",
}

IFSC_LIKE = re.compile(r'^[A-Z]{4}0[A-Z0-9]{6}$')
PURELY_NUMERIC = re.compile(r'^\d+$')
ALNUM_CODE_LIKE = re.compile(r'^[A-Z0-9]{6,}$')

RAIL_PREFIXES = [
    ("UPI", re.compile(r'^UPI[\s\-]', re.IGNORECASE)),
    ("NEFT", re.compile(r'^NEFT', re.IGNORECASE)),
    ("IMPS", re.compile(r'^IMPS', re.IGNORECASE)),
    ("FT", re.compile(r'^FT[\s\-]', re.IGNORECASE)),
    ("RTGS", re.compile(r'^R/', re.IGNORECASE)),
]

DASH_RUN = re.compile(r'\s+-\s+')
UPI_NAME = re.compile(r'^UPI[\s\-]+([^\-]+)-', re.IGNORECASE)


def _looks_like_code(segment: str) -> bool:
    """True if a segment is clearly a reference/bank/rail code, not a name."""
    s = segment.strip()
    if len(s) < 3:
        return True
    if PURELY_NUMERIC.match(s):
        return True
    if IFSC_LIKE.match(s.upper()):
        return True
    if s.lower() in KNOWN_BANK_CODES:
        return True
    words = s.lower().split()
    if words and all(w in RAIL_TOKENS for w in words):
        return True
    if ALNUM_CODE_LIKE.match(s) and any(c.isdigit() for c in s) and " " not in s:
        return True
    return False


def _name_score(segment: str) -> float:
    """Higher = more likely to be a real counterparty name."""
    s = segment.strip()
    if not s or _looks_like_code(s):
        return -1.0
    alpha_or_space = sum(c.isalpha() or c.isspace() for c in s)
    score = alpha_or_space / len(s)
    if " " in s:
        score += 0.3
    if len(s) >= 6:
        score += 0.1
    return score


def _best_segment(parts: list[str]) -> tuple[str | None, float]:
    best, best_score = None, 0.0
    for p in parts:
        sc = _name_score(p)
        if sc > best_score:
            best, best_score = p.strip(), sc
    return best, best_score


def parse_description(description: str | None) -> dict:
    """
    Extract counterparty name + rail type from a raw transaction description.

    Returns:
        {"rail_type": str, "counterparty_name": str | None,
         "confidence": "high" | "medium" | "low"}
    """
    if not description or not description.strip():
        return {"rail_type": "UNKNOWN", "counterparty_name": None, "confidence": "low"}

    text = description.strip()

    rail_type = "OTHER"
    for name, pattern in RAIL_PREFIXES:
        if pattern.match(text):
            rail_type = name
            break

    # UPI-<NAME>-<masked acct>-<ifsc>-<ref>-<timestamp>
    if rail_type == "UPI":
        m = UPI_NAME.match(text)
        if m:
            candidate = m.group(1).strip()
            if candidate and not _looks_like_code(candidate):
                return {"rail_type": rail_type, "counterparty_name": candidate.upper(), "confidence": "high"}

    # NEFT / FT dash-format: "NEFT  - X - Y - Z - NAME" (whitespace-padded dashes)
    if rail_type in ("NEFT", "FT") and DASH_RUN.search(text):
        parts = [p for p in DASH_RUN.split(text) if p.strip()]
        if len(parts) >= 2:
            candidate = parts[-1].strip()
            if not _looks_like_code(candidate):
                return {"rail_type": rail_type, "counterparty_name": candidate.upper(), "confidence": "high"}

    # Generic slash-delimited (NEFT/, IMPS/, IMPS OW/, R/...)
    if "/" in text:
        parts = [p for p in text.split("/") if p.strip()]
        best, best_score = _best_segment(parts)
        if best and best_score > 0.5:
            return {"rail_type": rail_type, "counterparty_name": best.upper(), "confidence": "medium"}

    # Generic bare-dash fallback (no surrounding whitespace, e.g. "FT-REF-NAME")
    if "-" in text:
        parts = [p for p in text.split("-") if p.strip()]
        best, best_score = _best_segment(parts)
        if best and best_score > 0.5:
            return {"rail_type": rail_type, "counterparty_name": best.upper(), "confidence": "medium"}

    return {"rail_type": rail_type, "counterparty_name": None, "confidence": "low"}
