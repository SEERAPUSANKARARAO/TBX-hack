"""
Few-Shot Examples for SQL Generation
=====================================
Targeted examples that teach the LLM how to translate bank-transaction
questions into correct DuckDB SQL against the bank/account/transaction
schema — including a multi-turn follow-up example, since reusing prior
filters correctly is a named must-have, not just a nice-to-have.
"""


FEW_SHOT_EXAMPLES = [
    # ── Example 1: Counterparty spend using the pre-aggregated view ──
    {
        "question": "How much did we send to Amazon Retail India this quarter?",
        "sql": """SELECT
    counterparty_name,
    SUM(total_spend) AS total_spend,
    SUM(transaction_count) AS transaction_count
FROM v_counterparty_spend_summary
WHERE counterparty_name ILIKE '%amazon retail india%'
  AND txn_year = YEAR(DATE '2026-09-05')
  AND txn_month IN (7, 8, 9)
GROUP BY counterparty_name;""",
        "explanation": "Uses the pre-aggregated view (avoids re-summing raw rows / fan-out) and ILIKE for partial, case-insensitive counterparty matching."
    },

    # ── Example 2: Account balance ──
    {
        "question": "What's our available balance at HDFC Bank?",
        "sql": """SELECT
    masked_account_number,
    bank_name,
    available_balance
FROM v_account_enriched
WHERE bank_name ILIKE '%hdfc%'
ORDER BY available_balance DESC;""",
        "explanation": "Reads from v_account_enriched, never the raw account table — account_number is already masked there."
    },

    # ── Example 3: Unreconciled transactions over a threshold ──
    {
        "question": "Show unreconciled transactions over 50000.",
        "sql": """SELECT
    transaction_id,
    transaction_date,
    counterparty_name,
    description,
    transaction_amount,
    reconciliation_proxy_status
FROM v_transaction_enriched
WHERE reconciliation_proxy_status = 'unreconciled'
  AND transaction_amount > 50000
ORDER BY transaction_amount DESC;""",
        "explanation": "reconciliation_proxy_status is a heuristic (no reference_id AND no UTR) — filters directly on it, includes the raw description so the user can verify."
    },

    # ── Example 4: Credit vs debit breakdown by bank ──
    {
        "question": "Break down total credits and debits by bank this year.",
        "sql": """SELECT
    bank_name,
    transaction_type,
    COUNT(*) AS transaction_count,
    SUM(transaction_amount) AS total_amount
FROM v_transaction_enriched
WHERE txn_year = YEAR(DATE '2026-09-05')
GROUP BY bank_name, transaction_type
ORDER BY bank_name, transaction_type;""",
        "explanation": "Simple GROUP BY on two dimensions using the enriched view's pre-joined bank_name."
    },

    # ── Example 5: Multi-turn follow-up — reuse filters, change only the date ──
    {
        "question": (
            "Turn 1: \"How much did we send to Bharti Airtel Limited last month?\"\n"
            "Turn 2 (follow-up): \"and this month?\""
        ),
        "sql": """-- Turn 1
SELECT counterparty_name, SUM(total_spend) AS total_spend, SUM(transaction_count) AS transaction_count
FROM v_counterparty_spend_summary
WHERE counterparty_name ILIKE '%bharti airtel limited%'
  AND txn_year = 2026 AND txn_month = 8
GROUP BY counterparty_name;

-- Turn 2 (follow-up: same counterparty filter, only the month changes)
SELECT counterparty_name, SUM(total_spend) AS total_spend, SUM(transaction_count) AS transaction_count
FROM v_counterparty_spend_summary
WHERE counterparty_name ILIKE '%bharti airtel limited%'
  AND txn_year = 2026 AND txn_month = 9
GROUP BY counterparty_name;""",
        "explanation": "On a follow-up question, keep every filter from the previous turn (counterparty, direction, table) and change only what the follow-up explicitly asks to change — here, just the month."
    },

    # ── Example 6: Anomaly-adjacent — largest transactions for a counterparty ──
    {
        "question": "What's the largest payment we've made to Selection Electronics?",
        "sql": """SELECT
    transaction_id,
    transaction_date,
    transaction_amount,
    description
FROM v_transaction_enriched
WHERE counterparty_name ILIKE '%selection electronics%'
  AND transaction_type = 'debit'
ORDER BY transaction_amount DESC
LIMIT 5;""",
        "explanation": "Row-level detail query (not aggregation) — uses v_transaction_enriched directly, filtered to the spend direction."
    },
]


def format_few_shot_messages() -> list[dict]:
    """
    Format few-shot examples as alternating user/assistant messages
    for injection into the LLM conversation.
    """
    messages = []
    for example in FEW_SHOT_EXAMPLES:
        messages.append({"role": "user", "content": f"Question: {example['question']}"})
        messages.append({"role": "assistant", "content": f"```sql\n{example['sql']}\n```"})
    return messages
