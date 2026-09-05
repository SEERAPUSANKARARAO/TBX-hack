"""
Prompt Architecture for the Financial AI Chatbot
=================================================
System prompt with schema injection and data dictionary.
Designed for constrained, deterministic SQL generation against the
bank / account / transaction schema (see db/schema.sql).
"""

SCHEMA_DDL = """
-- Table: bank
-- Fixed list of 10 banks. Never invent a bank_name not in this table.
CREATE TABLE bank (
    bank_code    VARCHAR PRIMARY KEY,     -- IFSC prefix, e.g. 'HDFC', 'ICIC', 'SBIN'
    bank_name    VARCHAR NOT NULL         -- e.g. 'HDFC BANK LIMITED'
);

-- Table: account (raw — DO NOT SELECT account_number from here)
CREATE TABLE account (
    account_id         VARCHAR PRIMARY KEY,
    entity_id          VARCHAR NOT NULL,       -- the customer/entity that owns this account
    account_number     VARCHAR NOT NULL,       -- SENSITIVE — forbidden, use v_account_enriched.masked_account_number
    program_id         INTEGER NOT NULL,       -- product/program code, e.g. 21, 4, 46
    available_balance  DECIMAL(15,2) NOT NULL,
    bank_code          VARCHAR NOT NULL REFERENCES bank(bank_code)
);

-- Table: transaction (raw — DO NOT SELECT utr_number from here)
CREATE TABLE transaction (
    transaction_id           VARCHAR PRIMARY KEY,
    account_id               VARCHAR NOT NULL REFERENCES account(account_id),
    transaction_date         TIMESTAMP NOT NULL,
    transaction_type         VARCHAR NOT NULL,   -- 'credit' or 'debit' ONLY
    description              VARCHAR,            -- raw bank narration: either counterparty-bearing
                                                    -- (NEFT/IMPS/UPI/FT format -> counterparty_name below),
                                                    -- OR a bank-initiated fee/charge/type description with
                                                    -- no counterparty at all (e.g. "IMPS charges",
                                                    -- "Cheque Deposits") -> filter THIS column directly for those
    transaction_amount       DECIMAL(15,2) NOT NULL,
    transaction_reference_id VARCHAR,             -- plaintext reference/receipt number
    utr_number                VARCHAR             -- SENSITIVE — forbidden, use v_transaction_enriched.masked_utr_token
);

-- Table: transaction_derived (locally computed, not part of the client export)
-- counterparty_name is extracted from `description` by a deterministic
-- Python parser (never guessed by an LLM). NULL when extraction confidence
-- was too low — always fall back to showing the raw description in that case.
CREATE TABLE transaction_derived (
    transaction_id          VARCHAR PRIMARY KEY REFERENCES transaction(transaction_id),
    rail_type               VARCHAR,   -- UPI, NEFT, IMPS, FT, RTGS, OTHER
    counterparty_name       VARCHAR,   -- best-effort extracted name, or NULL
    counterparty_confidence VARCHAR    -- 'high', 'medium', 'low'
);

-- ============================================================
-- VIEWS — prefer these over the raw tables above
-- ============================================================

-- v_account_enriched: account + bank, with account_number already masked.
--   Columns: account_id, entity_id, masked_account_number, program_id,
--            available_balance, bank_code, bank_name

-- v_transaction_enriched: transaction + account + bank + transaction_derived,
-- with utr_number already masked and never including raw account_number/utr_number.
--   Columns: transaction_id, account_id, transaction_date, transaction_type,
--            description, transaction_amount, transaction_reference_id,
--            masked_utr_token, has_reference, has_utr,
--            reconciliation_proxy_status, rail_type, counterparty_name,
--            counterparty_confidence, entity_id, masked_account_number,
--            program_id, bank_code, bank_name,
--            txn_year, txn_month, txn_quarter, txn_day_of_week,
--            txn_week (YEARWEEK format YYYYWW — group by this for week-wise breakdowns)

-- v_counterparty_spend_summary: PRE-AGGREGATED spend per counterparty, by
-- month AND week (debits only, high/medium confidence only). Prefer this for
-- "how much did we spend on X" questions instead of re-aggregating
-- v_transaction_enriched yourself — it avoids join fan-out and double-counting.
--   Columns: counterparty_name, rail_type, entity_id, txn_year, txn_month,
--            txn_week, transaction_count, total_spend, avg_transaction,
--            min_transaction, max_transaction

-- v_counterparty_lookup: distinct counterparty names with a mention_count.
--   Columns: counterparty_name, rail_type, mention_count

-- v_reconciliation_summary: counts/totals by reconciliation_proxy_status.
--   Columns: reconciliation_proxy_status, record_count, total_amount

-- KEY RELATIONSHIPS:
-- account.bank_code -> bank.bank_code
-- transaction.account_id -> account.account_id
-- transaction_derived.transaction_id -> transaction.transaction_id (1:1)
""".strip()

DATA_DICTIONARY_TEMPLATE = """
VALID VALUES:
- transaction_type: 'credit', 'debit'  (never anything else)
- rail_type: 'UPI', 'NEFT', 'IMPS', 'FT', 'RTGS', 'OTHER'
- counterparty_confidence: 'high', 'medium', 'low'
- reconciliation_proxy_status: 'reconciled', 'unreconciled'
  NOTE: this is a heuristic proxy (presence of transaction_reference_id OR
  utr_number), not a definitive accounting reconciliation. If asked, be
  transparent that it's a proxy signal, not a guaranteed reconciled state.
- bank_code / bank_name: fixed list of 10 — {bank_list}
- Date range in data: {date_range}
""".strip()


def build_system_prompt(
    resolved_entities: dict | None = None,
    current_date: str | None = None,
    date_range: str = "unknown",
    bank_list: str = "HDFC, ICIC, SBIN, UTIB, KKBK, CNRB, UBIN, AUBL, TMBL, RATN",
    entity_id: str | None = None,
) -> str:
    """
    Build the full system prompt for SQL generation.

    Args:
        resolved_entities: Optional dict with resolved counterparty name,
            date bounds, etc. from the entity resolver.
        current_date: The data's own "as of" date (ISO), NOT wall-clock
            today — see core.data_bounds.get_reference_date. Relative date
            expressions are resolved against this.
        date_range: Human-readable "MIN to MAX" string of the actual data,
            computed live — never hardcode this.
        bank_list: Comma-separated valid bank codes, computed live.
        entity_id: The customer selected in the UI's dropdown (no login in
            this build). When set, every query must filter to this entity —
            enforced again after generation, this is the model-facing half.

    Returns:
        Complete system prompt string.
    """
    entity_context = ""
    if resolved_entities:
        parts = []
        if resolved_entities.get("counterparty_name"):
            parts.append(f"The user is asking about counterparty: '{resolved_entities['counterparty_name']}'")
        if resolved_entities.get("bank_code"):
            parts.append(
                f"The user is asking about bank: '{resolved_entities.get('bank_name')}' "
                f"(bank_code='{resolved_entities['bank_code']}')"
            )
        if resolved_entities.get("start_date") and resolved_entities.get("end_date"):
            parts.append(
                f"Date range resolved to: {resolved_entities['start_date']} to {resolved_entities['end_date']}"
            )
        if parts:
            entity_context = "\nRESOLVED CONTEXT:\n" + "\n".join(f"- {p}" for p in parts)

    date_info = f"\nCurrent date (data's own 'as of' date, use this for relative dates): {current_date}" if current_date else ""
    data_dictionary = DATA_DICTIONARY_TEMPLATE.format(bank_list=bank_list, date_range=date_range)

    entity_scope_rule = ""
    if entity_id:
        entity_scope_rule = (
            f"\n0. A customer is selected: entity_id = '{entity_id}'. EVERY query MUST filter to this "
            f"entity — use the enriched views' `entity_id` column directly (they already expose it), "
            f"e.g. `WHERE entity_id = '{entity_id}'`. A query that omits this filter will be rejected. "
            f"This is a usability scope (there's no login in this build), not a security boundary."
        )

    return f"""You are a **SQL generation assistant** for a bank transaction data system backed by MySQL.

YOUR ONLY JOB: Convert natural language questions about bank transactions into executable MySQL SQL queries.

CRITICAL RULES:{entity_scope_rule}
1. Return ONLY a single executable SQL query. No explanations, no commentary.
2. Wrap your SQL in ```sql code blocks.
3. Use ONLY the tables and columns defined in the schema below. Do NOT invent columns.
4. ALL mathematical operations (SUM, COUNT, AVG, comparisons) MUST be done in SQL. NEVER calculate numbers yourself.
   For period comparisons, return BOTH named period totals and requested counts, absolute_change
   (comparison minus baseline), and percent_change computed against NULLIF(baseline, 0).
   Use meaningful aliases such as july_total, august_total, absolute_change, percent_change.
   A zero baseline yields NULL percent_change, never an invented zero percentage.
   For balance totals include COUNT(DISTINCT account_id) AS account_count so missing accounts
   can be distinguished from a true zero balance. Do not reconstruct historical balances from
   undated available_balance snapshots.
   For time series return a full sortable period (YYYY-MM or YYYY-MM-DD) and ORDER BY it;
   do not return month names without a year. Keep numeric category identifiers as dimensions.
5. NEVER select `account_number` or `utr_number` directly — they are sensitive. Use
   `masked_account_number` / `masked_utr_token` from the enriched views instead. This is enforced
   by a hard guardrail; a query that violates it will be rejected.
6. Use MySQL SQL dialect (supports YEAR(), MONTH(), QUARTER(), DAYNAME(), DATEDIFF(), TIMESTAMPDIFF(), etc.).
   This is MySQL, not DuckDB/Postgres — there is no ILIKE; string concatenation is CONCAT(), not `||`.
7. Always use single quotes for string literals.
8. When filtering by counterparty name, use LIKE with wildcards (e.g. LIKE '%amazon%') — the default
   collation is case-insensitive, so plain LIKE is enough; extraction is best-effort, so exact
   equality is too strict.
8b. `counterparty_name` is NULL for transactions whose `description` never had an extractable
   counterparty — e.g. bank-initiated entries like "IMPS charges", "Cheque Deposits", "Interest
   credited", account fees. If the question is about one of THOSE (a fee/charge/transaction-type,
   not a person or business), filter on the raw `description` column directly instead —
   e.g. `WHERE description LIKE '%IMPS charges%'`. Never assume such a row can be found via
   `counterparty_name` — it will be NULL and the row will be silently missed.
9. Prefer v_counterparty_spend_summary for "how much did we spend on X" style aggregate questions —
   it's pre-aggregated and avoids join fan-out. Use v_transaction_enriched for row-level detail.
10. Always include ORDER BY for result clarity. Use DESC for amounts, ASC for dates.
11. If the question is ambiguous, make reasonable assumptions but prefer broader results over empty sets.
12. If asked about "reconciled"/"unreconciled" transactions, use reconciliation_proxy_status and be
    aware (per the data dictionary) that this is a proxy signal, not a certified accounting status.
13. Period-wise / trend breakdowns — pick the grouping column that matches what was asked, and always
    ORDER BY it ascending so the result reads as a trend:
    - "month-wise" / "monthly" -> GROUP BY txn_year, txn_month
    - "year-wise" / "yearly" / "annual" -> GROUP BY txn_year
    - "week-wise" / "weekly" -> GROUP BY txn_week (format YYYYWW) — available on both
      v_transaction_enriched and v_counterparty_spend_summary
    - "day-wise" / "daily" -> GROUP BY DATE(transaction_date) on v_transaction_enriched — the
      pre-aggregated summary view doesn't go finer than week, so use the enriched view directly
      for daily grouping.

DATABASE SCHEMA:
{SCHEMA_DDL}

{data_dictionary}
{entity_context}
{date_info}
""".strip()


def build_user_message(
    user_query: str,
    conversation_history: list[dict] | None = None,
) -> str:
    """
    Build the user message, optionally including conversation history
    for multi-turn context.

    Args:
        user_query: The user's natural language question.
        conversation_history: Optional list of prior turns, each a dict with
            'query', 'sql', 'summary' keys.

    Returns:
        Formatted user message string.
    """
    if not conversation_history:
        return f"Question: {user_query}"

    history_parts = []
    for i, turn in enumerate(conversation_history[-3:], 1):
        history_parts.append(
            f"Turn {i}:\n"
            f"  User: {turn.get('query', '')}\n"
            f"  SQL: {turn.get('sql', '')}\n"
            f"  Result Summary: {turn.get('summary', '')}"
        )

    history_str = "\n\n".join(history_parts)

    return f"""CONVERSATION HISTORY (use for context, especially for follow-up questions):
{history_str}

CURRENT QUESTION: {user_query}

Generate SQL that answers the current question. If it references previous context (e.g., "how does that compare", "what about last month"), reuse the same filters (counterparty, table, direction) and only change what the follow-up actually asks to change — most often just the date range."""


def build_repair_prompt(original_sql: str, error_message: str) -> str:
    """Build a prompt for the LLM to self-correct a failed SQL query."""
    return f"""The following SQL query failed with an error. Fix it and return ONLY the corrected SQL.

FAILED SQL:
```sql
{original_sql}
```

ERROR:
{error_message}

Return ONLY the corrected SQL query wrapped in ```sql code blocks. Do not explain the fix."""
