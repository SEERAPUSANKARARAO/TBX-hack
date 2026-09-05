"""
Few-Shot Examples for SQL Generation
=====================================
Targeted examples that teach the LLM how to translate financial
questions into correct DuckDB SQL. Each example demonstrates a
different query pattern the system needs to handle.
"""


FEW_SHOT_EXAMPLES = [
    # ── Example 1: Simple vendor spend with SUM ──
    {
        "question": "How much did we spend on Amazon Web Services last quarter?",
        "sql": """SELECT
    vendor_name,
    SUM(amount) AS total_spend,
    COUNT(*) AS transaction_count
FROM transactions
WHERE LOWER(vendor_name) = LOWER('Amazon Web Services Inc.')
  AND QUARTER(transaction_date) = QUARTER(CURRENT_DATE) - 1
  AND YEAR(transaction_date) = YEAR(CURRENT_DATE)
GROUP BY vendor_name;""",
        "explanation": "Simple vendor filter with date range using QUARTER(). Uses LOWER() for case-insensitive matching."
    },

    # ── Example 2: Monthly spend breakdown with GROUP BY ──
    {
        "question": "Show me our monthly software subscription costs for 2026",
        "sql": """SELECT
    YEAR(transaction_date) AS year,
    MONTH(transaction_date) AS month,
    SUM(amount) AS total_spend,
    COUNT(*) AS num_transactions
FROM transactions
WHERE category = 'Software & Subscriptions'
  AND YEAR(transaction_date) = 2026
GROUP BY YEAR(transaction_date), MONTH(transaction_date)
ORDER BY year, month;""",
        "explanation": "Monthly aggregation using GROUP BY on date parts. Filters by expense category."
    },

    # ── Example 3: Join — payouts with reconciliation status ──
    {
        "question": "Show all unreconciled transactions with their details",
        "sql": """SELECT
    r.reconciliation_id,
    t.transaction_id,
    t.transaction_date,
    t.vendor_name,
    t.amount AS transaction_amount,
    t.category,
    r.variance,
    r.variance_reason,
    r.notes
FROM reconciliation_status r
JOIN transactions t ON r.transaction_id = t.transaction_id
WHERE r.status = 'unreconciled'
ORDER BY t.amount DESC;""",
        "explanation": "Joins reconciliation_status with transactions. Filters on reconciliation status enum."
    },

    # ── Example 4: Reconciliation analysis with variance ──
    {
        "question": "What is the total unreconciled amount and which vendors have the highest variance?",
        "sql": """SELECT
    t.vendor_name,
    COUNT(*) AS unreconciled_count,
    SUM(t.amount) AS total_unreconciled_amount,
    SUM(r.variance) AS total_variance
FROM reconciliation_status r
JOIN transactions t ON r.transaction_id = t.transaction_id
WHERE r.status IN ('unreconciled', 'pending')
GROUP BY t.vendor_name
ORDER BY total_unreconciled_amount DESC;""",
        "explanation": "Groups unreconciled/pending records by vendor. Aggregates both amounts and variances."
    },

    # ── Example 5: Complex multi-table — vendor spend vs payouts comparison ──
    {
        "question": "Compare total transactions vs total payouts for each vendor this year",
        "sql": """WITH txn_totals AS (
    SELECT
        vendor_id,
        vendor_name,
        SUM(amount) AS total_transactions,
        COUNT(*) AS txn_count
    FROM transactions
    WHERE YEAR(transaction_date) = 2026
    GROUP BY vendor_id, vendor_name
),
payout_totals AS (
    SELECT
        vendor_id,
        SUM(amount) AS total_payouts,
        COUNT(*) AS payout_count,
        SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END) AS completed_payouts
    FROM vendor_payouts
    WHERE YEAR(payout_date) = 2026
    GROUP BY vendor_id
)
SELECT
    t.vendor_name,
    t.total_transactions,
    t.txn_count,
    COALESCE(p.total_payouts, 0) AS total_payouts,
    COALESCE(p.payout_count, 0) AS payout_count,
    t.total_transactions - COALESCE(p.completed_payouts, 0) AS outstanding_balance
FROM txn_totals t
LEFT JOIN payout_totals p ON t.vendor_id = p.vendor_id
ORDER BY outstanding_balance DESC;""",
        "explanation": "CTEs comparing transaction totals vs payout totals per vendor. LEFT JOIN to catch vendors with transactions but no payouts."
    },
]


def format_few_shot_messages() -> list[dict]:
    """
    Format few-shot examples as alternating user/assistant messages
    for injection into the LLM conversation.

    Returns:
        List of message dicts with 'role' and 'content' keys.
    """
    messages = []
    for example in FEW_SHOT_EXAMPLES:
        messages.append({
            "role": "user",
            "content": f"Question: {example['question']}"
        })
        messages.append({
            "role": "assistant",
            "content": f"```sql\n{example['sql']}\n```"
        })
    return messages
