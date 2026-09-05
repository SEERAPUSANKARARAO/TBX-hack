"""
Entity Resolver — Vendor Matching & Date Parsing
=================================================
Preprocesses user queries to ground ambiguous mentions before
LLM generation. Resolves vendor names via fuzzy matching and
converts relative date expressions to explicit ISO date bounds.
"""
import re
from datetime import datetime, date
from dataclasses import dataclass, field

import duckdb
from rapidfuzz import fuzz, process


# Suffixes and words that strongly indicate a company or organization name
COMPANY_INDICATORS = {
    "corp", "corp.", "corporation", "inc", "inc.", "incorporated",
    "llc", "ltd", "ltd.", "limited", "co", "co.", "company",
    "group", "technologies", "solutions", "enterprises", "partners",
    "systems", "services", "industries", "holdings", "ventures", "agency", "labs",
}

# English grammar, SQL terms, and financial keywords that should NEVER be extracted as vendor names
NON_VENDOR_WORDS = {
    # Question words
    "who", "what", "where", "when", "why", "how", "which", "whose", "whom",
    # Verbs / Actions
    "show", "list", "give", "find", "get", "display", "tell", "compare",
    "calculate", "compute", "rank", "sort", "order", "group", "filter",
    "include", "exclude", "summarize", "aggregate", "check", "verify",
    "are", "is", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "can", "could", "would", "should", "will", "shall",
    # Prepositions & Conjunctions
    "the", "a", "an", "and", "or", "but", "for", "from", "with", "without",
    "in", "on", "at", "by", "to", "of", "about", "into", "over", "under",
    "above", "below", "between", "among", "through", "after", "before",
    "this", "that", "these", "those", "all", "any", "some", "every", "each",
    "our", "my", "your", "their", "its", "we", "us", "they", "them", "it",
    # Financial & aggregation terms
    "top", "bottom", "highest", "lowest", "most", "least", "best", "worst",
    "total", "sum", "average", "avg", "mean", "median", "count", "amount",
    "spend", "spending", "spent", "cost", "costs", "expense", "expenses",
    "expenditure", "payout", "payouts", "paid", "pay", "payment", "payments",
    "transaction", "transactions", "tx", "txs", "record", "records",
    "vendor", "vendors", "supplier", "suppliers", "merchant", "merchants",
    "account", "accounts", "chart", "category", "categories", "status",
    "reconciled", "unreconciled", "pending", "reconciliation", "reconcile",
    "breakdown", "summary", "report", "detail", "details", "overview",
    "table", "view", "database", "data", "row", "rows", "column", "columns",
    "month", "monthly", "year", "yearly", "annual", "quarter", "quarterly",
    "day", "daily", "week", "weekly", "date", "dates", "time", "period",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "q1", "q2", "q3", "q4", "ytd", "usd", "dollar", "dollars",
}


@dataclass
class ResolvedEntities:
    """Result of entity resolution on a user query."""
    vendor_name: str | None = None
    vendor_id: int | None = None
    vendor_match_score: float = 0.0
    start_date: str | None = None
    end_date: str | None = None
    date_expression: str | None = None
    unresolved_vendor: str | None = None  # If fuzzy match was too low
    raw_query: str = ""

    def to_dict(self) -> dict:
        """Convert to dict for prompt injection."""
        result = {}
        if self.vendor_name:
            result["vendor_name"] = self.vendor_name
            result["vendor_id"] = self.vendor_id
            result["vendor_match_score"] = self.vendor_match_score
        if self.start_date:
            result["start_date"] = self.start_date
            result["end_date"] = self.end_date
        if self.unresolved_vendor:
            result["unresolved_vendor"] = self.unresolved_vendor
        return result


class EntityResolver:
    """
    Resolves vendor names and date expressions from user queries.

    Usage:
        resolver = EntityResolver("path/to/financial.duckdb")
        result = resolver.resolve("How much did we spend on AWS last month?")
    """

    def __init__(self, db_path: str, fuzzy_threshold: int = 90):
        """
        Initialize the entity resolver.

        Args:
            db_path: Path to the DuckDB database.
            fuzzy_threshold: Minimum fuzzy match score (0-100) to accept a vendor match.
        """
        self.db_path = db_path
        self.fuzzy_threshold = fuzzy_threshold
        self.vendors = self._load_vendors()

    def _load_vendors(self) -> list[dict]:
        """Load vendor names and aliases from the database."""
        try:
            con = duckdb.connect(self.db_path, read_only=True)
            rows = con.execute("""
                SELECT vendor_id, vendor_name, vendor_alias, category
                FROM vendor_list
                WHERE is_active = true
            """).fetchall()
            con.close()

            vendors = []
            for vid, name, alias, cat in rows:
                vendors.append({
                    "vendor_id": vid,
                    "vendor_name": name,
                    "vendor_alias": alias,
                    "category": cat,
                })
            return vendors
        except Exception:
            return []

    def _build_search_index(self) -> dict[str, dict]:
        """
        Build a lookup dict mapping all possible name variants
        to their vendor record. This lets us fuzzy-match against
        both full names and aliases.
        """
        index = {}
        for vendor in self.vendors:
            index[vendor["vendor_name"].lower()] = vendor
            if vendor["vendor_alias"]:
                index[vendor["vendor_alias"].lower()] = vendor
        return index

    def resolve_vendor(self, query: str) -> tuple[dict | None, str | None]:
        """
        Attempt to identify which vendor the user is asking about.

        Args:
            query: The user's natural language query.

        Returns:
            Tuple of (matched vendor dict or None, unresolved vendor mention or None).
        """
        search_index = self._build_search_index()
        query_lower = query.lower()

        # ── Pass 1: Exact substring match ──
        for key, vendor in search_index.items():
            if len(key) <= 3:
                # Word boundary check for short acronyms like "AWS"
                if re.search(rf'\b{re.escape(key)}\b', query_lower):
                    return vendor, None
            elif key in query_lower:
                return vendor, None

        # ── Pass 2: Fuzzy match candidate mentions ──
        potential_mentions = self._extract_vendor_mentions(query)
        best_match = None
        best_score = 0

        for mention, _ in potential_mentions:
            result = process.extractOne(
                mention.lower(),
                list(search_index.keys()),
                scorer=fuzz.token_sort_ratio,
                score_cutoff=self.fuzzy_threshold,
            )
            if result and result[1] > best_score:
                best_match = search_index[result[0]]
                best_score = result[1]

        # Also check whole query for partial fuzzy match of vendor names
        if not best_match:
            for key, vendor in search_index.items():
                if len(key) > 3:
                    ratio = fuzz.partial_ratio(key, query_lower)
                    if ratio >= max(self.fuzzy_threshold, 85) and ratio > best_score:
                        best_match = vendor
                        best_score = ratio

        if best_match:
            return best_match, None

        # ── Pass 3: Check if user mentioned an explicit unknown vendor target ──
        for mention, is_explicit in potential_mentions:
            if is_explicit and len(mention) > 2 and mention.lower() not in NON_VENDOR_WORDS:
                return None, mention

        return None, None

    def _extract_vendor_mentions(self, query: str) -> list[tuple[str, bool]]:
        """
        Extract potential vendor name mentions from the query.
        Returns list of (mention_str, is_explicit_vendor_target).
        """
        mentions = []

        # 1. Quoted strings (e.g. "Acme Corp", 'Quantum Dynamics')
        for q in re.findall(r'["\']([^"\']+)["\']', query):
            q_clean = q.strip()
            if q_clean and q_clean.lower() not in NON_VENDOR_WORDS:
                mentions.append((q_clean, True))

        # 2. Explicit preposition patterns (e.g., "spend on Quantum Dynamics", "paid to Acme")
        prep_patterns = [
            r'\b(?:spend(?:ing)?\s+(?:on|for)|paid\s+to|payments?\s+to|payouts?\s+to|vendor)\s+([A-Za-z0-9\s&]+?)(?:\s+(?:in|for|last|this|q[1-4]|during|between|with|over|under|\?|$))',
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
            reference_date: Reference date for relative calculations. Defaults to today.

        Returns:
            Tuple of (start_date, end_date, matched_expression) or (None, None, None).
        """
        if reference_date is None:
            reference_date = date.today()

        query_lower = query.lower()

        # ── Month names ──
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4,
            "jun": 6, "jul": 7, "aug": 8, "sep": 9,
            "oct": 10, "nov": 11, "dec": 12,
        }
        for month_name, month_num in months.items():
            if month_name in query_lower:
                # Check for year mention
                year_match = re.search(r'\b(20\d{2})\b', query)
                year = int(year_match.group(1)) if year_match else reference_date.year

                start = date(year, month_num, 1)
                # End of month
                if month_num == 12:
                    end = date(year + 1, 1, 1)
                else:
                    end = date(year, month_num + 1, 1)
                from datetime import timedelta
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
            from datetime import timedelta
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
                    from datetime import timedelta
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
                    from datetime import timedelta
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
        Full entity resolution: vendor matching + date parsing.

        Args:
            query: The user's natural language query.
            reference_date: Reference date for relative date calculations.

        Returns:
            ResolvedEntities with all resolved context.
        """
        result = ResolvedEntities(raw_query=query)

        # Resolve vendor
        vendor, unresolved = self.resolve_vendor(query)
        if vendor:
            result.vendor_name = vendor["vendor_name"]
            result.vendor_id = vendor["vendor_id"]
            result.vendor_match_score = 100.0  # Exact or fuzzy-accepted
        elif unresolved:
            result.unresolved_vendor = unresolved

        # Resolve dates
        start_date, end_date, date_expr = self.resolve_dates(query, reference_date)
        if start_date:
            result.start_date = start_date
            result.end_date = end_date
            result.date_expression = date_expr

        return result
