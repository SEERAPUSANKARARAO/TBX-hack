"""
Entity Resolver — Counterparty Matching & Date Parsing
=======================================================
Preprocesses user queries to ground ambiguous mentions before
LLM generation. Resolves counterparty names (fuzzy-matched against
names extracted from real transaction narration by
core/description_parser.py — there is no clean vendor_list table in
the real client schema) and converts relative date expressions into
explicit ISO date bounds.
"""
import re
import logging
from datetime import datetime, date, timedelta
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from core.db_connection import get_connection

logger = logging.getLogger(__name__)

# Real usage very often drops the legal suffix ("Bharti Airtel" for "BHARTI
# AIRTEL LIMITED") — stripped before fuzzy comparison so that drop doesn't
# cost enough score to fall below the match threshold. Applied only to the
# comparison key, never to the name actually returned/displayed.
_LEGAL_SUFFIX_RE = re.compile(
    r'\b(private\s+limited|pvt\.?\s*ltd\.?|limited|ltd\.?|llc|inc\.?|incorporated|'
    r'corporation|corp\.?|company|co\.?)\b\.?\s*$',
    re.IGNORECASE,
)


def _normalize_for_match(name: str) -> str:
    """Strip trailing legal-entity suffixes for fuzzy comparison purposes."""
    s = name.strip()
    prev = None
    while prev != s:
        prev = s
        s = _LEGAL_SUFFIX_RE.sub('', s).strip()
    return s.lower() or name.strip().lower()


# Fixed list of 10 banks (see db/schema.sql) with the colloquial names people
# actually say, since almost nobody says the IFSC-prefix bank_code out loud
# except HDFC/ICICI-style ones that already read naturally. Hardcoding this
# is appropriate for a closed, client-specified list — not a general NLU model.
BANK_ALIASES = {
    "HDFC": ["hdfc"],
    "ICIC": ["icici"],
    "SBIN": ["sbi", "state bank of india", "state bank"],
    "UTIB": ["axis"],
    "KKBK": ["kotak", "kotak mahindra"],
    "CNRB": ["canara"],
    "UBIN": ["union bank of india", "union bank"],
    "AUBL": ["au small finance", "au bank"],
    "TMBL": ["tamilnad mercantile", "tmb"],
    "RATN": ["rbl"],
}

# Suffixes and words that strongly indicate a company or organization name
COMPANY_INDICATORS = {
    "corp", "corp.", "corporation", "inc", "inc.", "incorporated",
    "llc", "ltd", "ltd.", "limited", "co", "co.", "company",
    "group", "technologies", "solutions", "enterprises", "partners",
    "systems", "services", "industries", "holdings", "ventures", "agency", "labs",
}

# English grammar, SQL terms, and financial keywords that should NEVER be extracted as a counterparty name
NON_VENDOR_WORDS = {
    "who", "what", "where", "when", "why", "how", "which", "whose", "whom",
    "show", "list", "give", "find", "get", "display", "tell", "compare",
    "calculate", "compute", "rank", "sort", "order", "group", "filter",
    "include", "exclude", "summarize", "aggregate", "check", "verify",
    "are", "is", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "can", "could", "would", "should", "will", "shall",
    "the", "a", "an", "and", "or", "but", "for", "from", "with", "without",
    "in", "on", "at", "by", "to", "of", "about", "into", "over", "under",
    "above", "below", "between", "among", "through", "after", "before",
    "this", "that", "these", "those", "all", "any", "some", "every", "each",
    "our", "my", "your", "their", "its", "we", "us", "they", "them", "it",
    "top", "bottom", "highest", "lowest", "most", "least", "best", "worst",
    "total", "sum", "average", "avg", "mean", "median", "count", "amount",
    "spend", "spending", "spent", "cost", "costs", "expense", "expenses",
    "expenditure", "paid", "pay", "payment", "payments", "sent", "received",
    "transaction", "transactions", "tx", "txs", "record", "records",
    "counterparty", "counterparties", "vendor", "vendors", "merchant", "merchants",
    # Bare legal-entity suffixes — never a real name by themselves (e.g. a
    # quoted "'Ltd'" in "dropping 'Ltd'" must not be treated as the target).
    "ltd", "ltd.", "limited", "inc", "inc.", "llc", "corp", "corp.",
    "corporation", "pvt", "pvt.", "co", "co.", "company",
    "account", "accounts", "bank", "banks", "balance", "available",
    "reconciled", "unreconciled", "pending", "reconciliation", "reconcile",
    "reference", "utr", "ifsc", "transfer", "transferred", "credit", "debit",
    "breakdown", "summary", "report", "detail", "details", "overview",
    "table", "view", "database", "data", "row", "rows", "column", "columns",
    "month", "monthly", "year", "yearly", "annual", "quarter", "quarterly",
    "day", "daily", "week", "weekly", "date", "dates", "time", "period",
    "january", "february", "march", "april", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "q1", "q2", "q3", "q4", "ytd", "usd", "inr", "rupees", "rs", "dollar", "dollars",
}


@dataclass
class ResolvedEntities:
    """Result of entity resolution on a user query."""
    counterparty_name: str | None = None
    counterparty_match_score: float = 0.0
    bank_code: str | None = None
    bank_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    date_expression: str | None = None
    unresolved_counterparty: str | None = None  # If fuzzy match was too low
    raw_query: str = ""

    def to_dict(self) -> dict:
        """Convert to dict for prompt injection."""
        result = {}
        if self.counterparty_name:
            result["counterparty_name"] = self.counterparty_name
            result["counterparty_match_score"] = self.counterparty_match_score
        if self.bank_code:
            result["bank_code"] = self.bank_code
            result["bank_name"] = self.bank_name
        if self.start_date:
            result["start_date"] = self.start_date
            result["end_date"] = self.end_date
        if self.unresolved_counterparty:
            result["unresolved_counterparty"] = self.unresolved_counterparty
        return result


class EntityResolver:
    """
    Resolves counterparty names and date expressions from user queries.
    Connects via core.db_connection (MySQL) — see that module to change
    where "the database" points.

    Usage:
        resolver = EntityResolver()
        result = resolver.resolve("How much did we send to Amazon last month?")
    """

    def __init__(self, fuzzy_threshold: int = 90):
        """
        Args:
            fuzzy_threshold: Minimum fuzzy match score (0-100) to accept a match.
        """
        self.fuzzy_threshold = fuzzy_threshold
        self.counterparties = self._load_counterparties()
        self.banks = self._load_banks()

    def _load_banks(self) -> dict[str, str]:
        """Load {bank_code: bank_name} from the fixed bank table."""
        try:
            con = get_connection(readonly=True)
            with con.cursor() as cur:
                cur.execute("SELECT bank_code, bank_name FROM bank")
                rows = cur.fetchall()
            con.close()
            return {code: name for code, name in rows}
        except Exception as e:
            logger.warning("Failed to load bank list: %s", e)
            return {}

    def resolve_bank(self, query: str) -> tuple[str, str] | None:
        """Match a known bank by colloquial alias or IFSC code. Returns (bank_code, bank_name) or None."""
        query_lower = query.lower()
        for code, aliases in BANK_ALIASES.items():
            for alias in aliases:
                if re.search(rf'\b{re.escape(alias)}\b', query_lower):
                    return code, self.banks.get(code, code)
        for code in self.banks:
            if re.search(rf'\b{re.escape(code.lower())}\b', query_lower):
                return code, self.banks[code]
        return None

    def _mention_matches_known_bank(self, mention: str) -> bool:
        """True if an 'unresolved counterparty' mention is actually a known bank reference."""
        mention_lower = mention.lower().strip()
        for code, aliases in BANK_ALIASES.items():
            if mention_lower == code.lower() or mention_lower in aliases:
                return True
            if any(mention_lower in alias or alias in mention_lower for alias in aliases):
                return True
        return False

    def _load_counterparties(self) -> list[dict]:
        """Load distinct counterparty names extracted from transaction narration."""
        try:
            con = get_connection(readonly=True)
            with con.cursor() as cur:
                cur.execute("SELECT counterparty_name, rail_type, mention_count FROM v_counterparty_lookup")
                rows = cur.fetchall()
            con.close()
            return [
                {"counterparty_name": name, "rail_type": rail, "mention_count": cnt}
                for name, rail, cnt in rows
            ]
        except Exception as e:
            logger.warning("Failed to load counterparty lookup: %s", e)
            return []

    def _build_search_index(self) -> dict[str, dict]:
        """Lookup dict mapping lowercased counterparty names to their record."""
        return {c["counterparty_name"].lower(): c for c in self.counterparties}

    def resolve_counterparty(self, query: str) -> tuple[dict | None, float, str | None]:
        """
        Attempt to identify which counterparty the user is asking about.

        Returns:
            Tuple of (matched record or None, match_score, unresolved mention or None).
        """
        search_index = self._build_search_index()
        query_lower = query.lower()

        # ── Pass 1: Exact substring match ──
        for key, record in search_index.items():
            if len(key) <= 3:
                if re.search(rf'\b{re.escape(key)}\b', query_lower):
                    return record, 100.0, None
            elif key in query_lower:
                return record, 100.0, None

        # Normalized (legal-suffix-stripped) index for fuzzy comparison only —
        # the returned record still carries the real, full counterparty_name.
        normalized_index: dict[str, dict] = {}
        for key, record in search_index.items():
            normalized_index[_normalize_for_match(key)] = record

        # ── Pass 2: Fuzzy match candidate mentions ──
        potential_mentions = self._extract_counterparty_mentions(query)
        best_match = None
        best_score = 0.0

        for mention, _ in potential_mentions:
            norm_mention = _normalize_for_match(mention)
            result = process.extractOne(
                norm_mention,
                list(normalized_index.keys()),
                scorer=fuzz.token_sort_ratio,
                score_cutoff=self.fuzzy_threshold,
            )
            if result and result[1] > best_score:
                best_match = normalized_index[result[0]]
                best_score = result[1]

        # Also check whole query for partial fuzzy match of counterparty names
        if not best_match:
            norm_query = _normalize_for_match(query_lower)
            for norm_key, record in normalized_index.items():
                if len(norm_key) > 3:
                    ratio = fuzz.partial_ratio(norm_key, norm_query)
                    if ratio >= max(self.fuzzy_threshold, 85) and ratio > best_score:
                        best_match = record
                        best_score = ratio

        if best_match:
            return best_match, float(best_score), None

        # ── Pass 3: Check if user mentioned an explicit unknown target ──
        for mention, is_explicit in potential_mentions:
            if (is_explicit and len(mention) > 2 and mention.lower() not in NON_VENDOR_WORDS
                    and not self._mention_matches_known_bank(mention)):
                return None, 0.0, mention

        return None, 0.0, None

    def _extract_counterparty_mentions(self, query: str) -> list[tuple[str, bool]]:
        """Extract potential counterparty name mentions. Returns (mention, is_explicit)."""
        mentions = []

        # 1. Quoted strings
        for q in re.findall(r'["\']([^"\']+)["\']', query):
            q_clean = q.strip()
            if q_clean and q_clean.lower() not in NON_VENDOR_WORDS:
                mentions.append((q_clean, True))

        # 2. Explicit preposition patterns (e.g. "sent to Amazon", "paid to Gautam Singh")
        prep_patterns = [
            r'\b(?:spend(?:ing)?\s+(?:on|for)|paid\s+to|payments?\s+to|sent\s+to|transferred?\s+to|from)\s+([A-Za-z0-9\s&]+?)(?:\s+(?:in|for|last|this|q[1-4]|during|between|with|over|under|\?|$))',
        ]
        for pat in prep_patterns:
            for match in re.findall(pat, query, re.IGNORECASE):
                m_clean = match.strip()
                words = [w for w in m_clean.split() if w.lower() not in NON_VENDOR_WORDS]
                if words:
                    cand = " ".join(words)
                    if len(cand) > 1:
                        mentions.append((cand, True))

        # 3. Capitalized sequences
        cap_pattern = r'\b([A-Z][A-Za-z0-9&]*(?:\s+[A-Z][A-Za-z0-9&]*)*)\b'
        caps = re.findall(cap_pattern, query)
        for cap in caps:
            cap_words = cap.split()
            non_generic = [w for w in cap_words if w.lower() not in NON_VENDOR_WORDS]
            if not non_generic:
                continue
            cand = " ".join(non_generic)
            has_company_suffix = any(w.lower() in COMPANY_INDICATORS for w in cap_words)
            is_explicit = has_company_suffix or len(cap_words) >= 2
            mentions.append((cand, is_explicit))

        return mentions

    def resolve_dates(self, query: str, reference_date: date | None = None) -> tuple[str | None, str | None, str | None]:
        """
        Parse relative date expressions into explicit ISO date bounds.

        Args:
            query: The user's natural language query.
            reference_date: Reference date for relative calculations — should be
                the data's own "as of" date (see core.data_bounds), not
                necessarily wall-clock today.

        Returns:
            Tuple of (start_date, end_date, matched_expression) or (None, None, None).
        """
        if reference_date is None:
            reference_date = date.today()

        query_lower = query.lower()

        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4,
            "jun": 6, "jul": 7, "aug": 8, "sep": 9,
            "oct": 10, "nov": 11, "dec": 12,
        }
        for month_name, month_num in months.items():
            if not re.search(rf'\b{month_name}\b', query_lower):
                continue
            # "may" is a common modal verb ("which transactions may be...") —
            # only treat it as the month if it looks date-ish: capitalized in
            # the original text, preceded by a preposition, or near a year.
            if month_name == "may":
                capitalized = re.search(r'\bMay\b', query)
                near_prep = re.search(r'\b(?:in|during|for|of|since|until|by)\s+may\b', query_lower)
                near_year = re.search(r'\bmay\b.{0,10}\b20\d{2}\b|\b20\d{2}\b.{0,10}\bmay\b', query_lower)
                if not (capitalized or near_prep or near_year):
                    continue

            year_match = re.search(r'\b(20\d{2})\b', query)
            year = int(year_match.group(1)) if year_match else reference_date.year

            start = date(year, month_num, 1)
            if month_num == 12:
                end = date(year + 1, 1, 1)
            else:
                end = date(year, month_num + 1, 1)
            end = end - timedelta(days=1)

            return start.isoformat(), end.isoformat(), month_name

        # ── "Last month" ──
        if "last month" in query_lower:
            if reference_date.month == 1:
                start = date(reference_date.year - 1, 12, 1)
                end = date(reference_date.year, 1, 1)
            else:
                start = date(reference_date.year, reference_date.month - 1, 1)
                end = date(reference_date.year, reference_date.month, 1)
            end = end - timedelta(days=1)
            return start.isoformat(), end.isoformat(), "last month"

        # ── "This month" ──
        if "this month" in query_lower:
            start = date(reference_date.year, reference_date.month, 1)
            end = reference_date
            return start.isoformat(), end.isoformat(), "this month"

        # ── Quarters ──
        quarter_map = {"q1": (1, 3), "q2": (4, 6), "q3": (7, 9), "q4": (10, 12)}
        for q_name, (start_m, end_m) in quarter_map.items():
            if q_name in query_lower:
                year_match = re.search(r'\b(20\d{2})\b', query)
                year = int(year_match.group(1)) if year_match else reference_date.year
                start = date(year, start_m, 1)
                if end_m == 12:
                    end = date(year, 12, 31)
                else:
                    end = date(year, end_m + 1, 1) - timedelta(days=1)
                return start.isoformat(), end.isoformat(), q_name.upper()

        # ── "Last quarter" ──
        if "last quarter" in query_lower:
            current_quarter = (reference_date.month - 1) // 3 + 1
            if current_quarter == 1:
                start = date(reference_date.year - 1, 10, 1)
                end = date(reference_date.year - 1, 12, 31)
            else:
                prev_q = current_quarter - 1
                start_m = (prev_q - 1) * 3 + 1
                end_m = prev_q * 3
                start = date(reference_date.year, start_m, 1)
                if end_m == 12:
                    end = date(reference_date.year, 12, 31)
                else:
                    end = date(reference_date.year, end_m + 1, 1) - timedelta(days=1)
            return start.isoformat(), end.isoformat(), "last quarter"

        # ── "This quarter" ──
        if "this quarter" in query_lower:
            current_quarter = (reference_date.month - 1) // 3 + 1
            start_m = (current_quarter - 1) * 3 + 1
            start = date(reference_date.year, start_m, 1)
            end = reference_date
            return start.isoformat(), end.isoformat(), "this quarter"

        # ── "This year" / "YTD" ──
        if "this year" in query_lower or "ytd" in query_lower or "year to date" in query_lower:
            start = date(reference_date.year, 1, 1)
            end = reference_date
            return start.isoformat(), end.isoformat(), "this year"

        # ── "Last year" ──
        if "last year" in query_lower:
            start = date(reference_date.year - 1, 1, 1)
            end = date(reference_date.year - 1, 12, 31)
            return start.isoformat(), end.isoformat(), "last year"

        return None, None, None

    def resolve(self, query: str, reference_date: date | None = None) -> ResolvedEntities:
        """
        Full entity resolution: counterparty matching + date parsing.

        Args:
            query: The user's natural language question.
            reference_date: Reference date for relative date calculations
                (pass the data's max transaction date, not wall-clock today).

        Returns:
            ResolvedEntities with all resolved context.
        """
        result = ResolvedEntities(raw_query=query)

        bank_match = self.resolve_bank(query)
        if bank_match:
            result.bank_code, result.bank_name = bank_match

        record, score, unresolved = self.resolve_counterparty(query)
        if record:
            result.counterparty_name = record["counterparty_name"]
            result.counterparty_match_score = score
        elif unresolved:
            result.unresolved_counterparty = unresolved

        start_date, end_date, date_expr = self.resolve_dates(query, reference_date)
        if start_date:
            result.start_date = start_date
            result.end_date = end_date
            result.date_expression = date_expr

        return result
