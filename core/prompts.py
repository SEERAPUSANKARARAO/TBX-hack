"""
Prompt Architecture for the Financial AI Chatbot
=================================================
System prompt with schema injection and data dictionary.
Designed for constrained, deterministic SQL generation.
"""

# ── Minimal DDL for prompt injection ──
# This is a compact representation of our schema, optimized for token efficiency.
# We include only the columns, types, and relationships the LLM needs.

SCHEMA_DDL = """
-- Table: chart_of_accounts
-- Master list of General Ledger accounts.
CREATE TABLE chart_of_accounts (
    account_id      INTEGER PRIMARY KEY,
    account_code    VARCHAR NOT NULL UNIQUE,    -- e.g. '5200', '1000'
    account_name    VARCHAR NOT NULL,           -- e.g. 'Software & Subscriptions', 'Cash and Cash Equivalents'
    account_type    VARCHAR NOT NULL,           -- One of: 'Asset', 'Liability', 'Equity', 'Revenue', 'Expense'
    parent_account_code VARCHAR,
    description     VARCHAR,
    is_active       BOOLEAN DEFAULT true
);

-- Table: vendor_list
-- Master vendor directory.
CREATE TABLE vendor_list (
    vendor_id       INTEGER PRIMARY KEY,
    vendor_name     VARCHAR NOT NULL,           -- Full legal name, e.g. 'Amazon Web Services Inc.'
    vendor_alias    VARCHAR,                    -- Short alias, e.g. 'AWS'
    category        VARCHAR,                    -- e.g. 'Cloud Infrastructure', 'Office Supplies', 'Professional Services'
    contact_email   VARCHAR,
    phone           VARCHAR,
    address         VARCHAR,
    payment_terms   VARCHAR,                    -- 'Net 15', 'Net 30', 'Net 45', 'Net 60'
    is_active       BOOLEAN DEFAULT true
);

-- Table: transactions
-- Core financial transaction ledger. Every expense/payment is recorded here.
CREATE TABLE transactions (
    transaction_id    INTEGER PRIMARY KEY,
    transaction_date  DATE NOT NULL,             -- Date the transaction occurred
    posted_date       DATE,                      -- Date it was posted to the ledger
    vendor_id         INTEGER REFERENCES vendor_list(vendor_id),
    vendor_name       VARCHAR NOT NULL,
    amount            DECIMAL(15, 2) NOT NULL,   -- Always positive; transaction_type indicates direction
    currency          VARCHAR DEFAULT 'USD',
    transaction_type  VARCHAR NOT NULL,          -- 'debit' or 'credit'
    account_code      VARCHAR REFERENCES chart_of_accounts(account_code),
    category          VARCHAR,                   -- e.g. 'Software & Subscriptions', 'Travel & Entertainment'
    description       VARCHAR,                   -- Human-readable description
    reference_number  VARCHAR,                   -- Invoice or PO number
    payment_method    VARCHAR                    -- 'ACH', 'Wire', 'Credit Card', 'Check'
);

-- Table: vendor_payouts
-- Records of payments issued TO vendors.
CREATE TABLE vendor_payouts (
    payout_id         INTEGER PRIMARY KEY,
    vendor_id         INTEGER REFERENCES vendor_list(vendor_id),
    vendor_name       VARCHAR NOT NULL,
    payout_date       DATE NOT NULL,
    amount            DECIMAL(15, 2) NOT NULL,
    currency          VARCHAR DEFAULT 'USD',
    payment_method    VARCHAR,
    bank_reference    VARCHAR,                   -- Bank transaction reference
    invoice_number    VARCHAR,                   -- Matching invoice/PO number
    status            VARCHAR NOT NULL,          -- 'completed', 'pending', 'failed'
    notes             VARCHAR
);

-- Table: reconciliation_status
-- Links transactions to payouts. Tracks whether each transaction has been matched to a payout.
CREATE TABLE reconciliation_status (
    reconciliation_id   INTEGER PRIMARY KEY,
    transaction_id      INTEGER REFERENCES transactions(transaction_id),
    payout_id           INTEGER REFERENCES vendor_payouts(payout_id),  -- NULL if unreconciled
    reconciliation_date DATE,
    status              VARCHAR NOT NULL,        -- 'reconciled', 'unreconciled', 'pending'
    matched_amount      DECIMAL(15, 2),
    variance            DECIMAL(15, 2) DEFAULT 0.00,  -- Difference between transaction and payout amounts
    variance_reason     VARCHAR,
    reconciled_by       VARCHAR,                 -- 'system' (auto-matched) or 'manual_review'
    notes               VARCHAR
);

-- KEY RELATIONSHIPS:
-- transactions.vendor_id -> vendor_list.vendor_id
-- transactions.account_code -> chart_of_accounts.account_code
-- vendor_payouts.vendor_id -> vendor_list.vendor_id
-- reconciliation_status.transaction_id -> transactions.transaction_id
-- reconciliation_status.payout_id -> vendor_payouts.payout_id (can be NULL)

-- USEFUL VIEWS AVAILABLE:
-- v_transactions_enriched: Transactions joined with vendor and account details (includes txn_year, txn_month, txn_quarter)
-- v_reconciliation_details: Full reconciliation view joining transactions and payouts
-- v_vendor_spend_summary: Monthly spend summary per vendor (vendor_id, vendor_name, spend_year, spend_month, transaction_count, total_spend, avg_transaction)
-- v_reconciliation_summary: Aggregate reconciliation stats by status
""".strip()

# ── Data Dictionary ──
# Valid values for key enum columns, injected into the prompt so the LLM
# generates correct filter predicates.

DATA_DICTIONARY = """
VALID VALUES:
- account_type: 'Asset', 'Liability', 'Equity', 'Revenue', 'Expense'
- transaction_type: 'debit', 'credit'
- payment_method: 'ACH', 'Wire', 'Credit Card', 'Check'
- vendor_payouts.status: 'completed', 'pending', 'failed'
- reconciliation_status.status: 'reconciled', 'unreconciled', 'pending'
- reconciliation_status.reconciled_by: 'system', 'manual_review'
- category (transactions): 'Software & Subscriptions', 'Office & Facilities', 'Travel & Entertainment', 'Insurance', 'Professional Services', 'Marketing & Advertising'
- Date range in data: 2026-01-01 to 2026-06-30
""".strip()


def build_system_prompt(
    resolved_entities: dict | None = None,
    current_date: str | None = None,
) -> str:
    """
    Build the full system prompt for SQL generation.

    Args:
        resolved_entities: Optional dict with resolved vendor names, date bounds, etc.
                          from the entity resolver.
        current_date: Current date string (ISO format) for relative date resolution.

    Returns:
        Complete system prompt string.
    """
    entity_context = ""
    if resolved_entities:
        parts = []
        if resolved_entities.get("vendor_name"):
            parts.append(
                f"The user is asking about vendor: '{resolved_entities['vendor_name']}' "
                f"(vendor_id={resolved_entities.get('vendor_id', 'unknown')})"
            )
        if resolved_entities.get("start_date") and resolved_entities.get("end_date"):
            parts.append(
                f"Date range resolved to: {resolved_entities['start_date']} to {resolved_entities['end_date']}"
            )
        if parts:
            entity_context = "\nRESOLVED CONTEXT:\n" + "\n".join(f"- {p}" for p in parts)

    date_info = f"\nCurrent date: {current_date}" if current_date else ""

    return f"""You are a **SQL generation assistant** for a financial data system backed by DuckDB.

YOUR ONLY JOB: Convert natural language questions about financial data into executable DuckDB SQL queries.

CRITICAL RULES:
1. Return ONLY a single executable SQL query. No explanations, no commentary.
2. Wrap your SQL in ```sql code blocks.
3. Use ONLY the tables and columns defined in the schema below. Do NOT invent columns.
4. ALL mathematical operations (SUM, COUNT, AVG, comparisons) MUST be done in SQL. NEVER calculate numbers yourself.
5. Use DuckDB SQL dialect (supports YEAR(), MONTH(), QUARTER(), DAYNAME(), DATE_DIFF(), etc.).
6. Always use single quotes for string literals.
7. When filtering by vendor name, use LOWER() for case-insensitive matching, or match on vendor_id.
8. For date filtering, use transaction_date (not posted_date) unless specifically asked about posting dates.
9. Prefer the pre-built views (v_transactions_enriched, v_reconciliation_details, v_vendor_spend_summary) when they simplify the query.
10. Always include ORDER BY for result clarity. Use DESC for amounts, ASC for dates.
11. If the question is ambiguous, make reasonable assumptions but prefer broader results over empty sets.

DATABASE SCHEMA:
{SCHEMA_DDL}

{DATA_DICTIONARY}
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

    # Build multi-turn context
    history_parts = []
    for i, turn in enumerate(conversation_history[-3:], 1):  # Last 3 turns max
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

Generate SQL that answers the current question. If it references previous context (e.g., "how does that compare", "what about last month"), modify the previous SQL logic accordingly."""


def build_repair_prompt(original_sql: str, error_message: str) -> str:
    """
    Build a prompt for the LLM to self-correct a failed SQL query.

    Args:
        original_sql: The SQL that failed.
        error_message: The error from DuckDB or the validator.

    Returns:
        Repair prompt string.
    """
    return f"""The following SQL query failed with an error. Fix it and return ONLY the corrected SQL.

FAILED SQL:
```sql
{original_sql}
```

ERROR:
{error_message}

Return ONLY the corrected SQL query wrapped in ```sql code blocks. Do not explain the fix."""
