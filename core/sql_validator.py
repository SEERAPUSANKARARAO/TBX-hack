"""
SQL Validator — Guardrails for Generated SQL
=============================================
Uses sqlglot to parse and validate SQL before execution.
Blocks destructive operations and verifies schema references.
"""
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp


# Tables and columns that exist in our schema
VALID_TABLES = {
    "transactions", "vendor_payouts", "reconciliation_status",
    "chart_of_accounts", "vendor_list",
    # Views
    "v_transactions_enriched", "v_reconciliation_details",
    "v_vendor_spend_summary", "v_reconciliation_summary",
    "v_vendor_lookup", "v_reconciliation_statuses",
    "v_expense_categories", "v_account_types",
}

VALID_COLUMNS = {
    # transactions
    "transaction_id", "transaction_date", "posted_date", "vendor_id",
    "vendor_name", "amount", "currency", "transaction_type", "account_code",
    "category", "description", "reference_number", "payment_method",
    # vendor_payouts
    "payout_id", "payout_date", "bank_reference", "invoice_number",
    "status", "notes",
    # reconciliation_status
    "reconciliation_id", "reconciliation_date", "matched_amount",
    "variance", "variance_reason", "reconciled_by",
    # chart_of_accounts
    "account_id", "account_name", "account_type",
    "parent_account_code", "is_active",
    # vendor_list
    "vendor_alias", "contact_email", "phone", "address", "payment_terms",
    # View columns
    "vendor_category", "expense_category", "txn_year", "txn_month",
    "txn_quarter", "txn_day_of_week",
    "reconciliation_status", "transaction_amount", "payout_amount",
    "payout_status", "transaction_ref",
    "spend_year", "spend_month", "transaction_count", "total_spend",
    "avg_transaction", "min_transaction", "max_transaction",
    "record_count", "total_variance", "avg_variance",
}

# SQL statements that are BLOCKED (destructive operations)
BLOCKED_STATEMENT_TYPES = {
    exp.Update, exp.Delete, exp.Drop, exp.Insert,
    exp.Alter, exp.Create,
}


@dataclass
class ValidationResult:
    """Result of SQL validation."""
    is_valid: bool
    sql: str
    error: str | None = None
    warnings: list[str] | None = None

    def __bool__(self):
        return self.is_valid


def extract_sql_from_response(llm_response: str) -> str:
    """
    Extract SQL from an LLM response that may contain markdown code blocks.

    Handles:
    - ```sql ... ``` blocks
    - ``` ... ``` blocks
    - Raw SQL text

    Args:
        llm_response: Raw LLM output.

    Returns:
        Extracted SQL string.
    """
    # Try to extract from ```sql ... ``` blocks
    sql_block_pattern = r"```sql\s*\n?(.*?)```"
    matches = re.findall(sql_block_pattern, llm_response, re.DOTALL | re.IGNORECASE)
    if matches:
        return matches[0].strip()

    # Try generic ``` ... ``` blocks
    generic_block_pattern = r"```\s*\n?(.*?)```"
    matches = re.findall(generic_block_pattern, llm_response, re.DOTALL)
    if matches:
        return matches[0].strip()

    # Assume the whole response is SQL, strip any leading/trailing text
    # Look for SELECT as the start of the query
    lines = llm_response.strip().split("\n")
    sql_lines = []
    in_sql = False
    for line in lines:
        stripped = line.strip().upper()
        if stripped.startswith(("SELECT", "WITH", "(")):
            in_sql = True
        if in_sql:
            sql_lines.append(line)

    if sql_lines:
        return "\n".join(sql_lines).strip().rstrip(";") + ";"

    # Last resort: return as-is
    return llm_response.strip()


def validate_sql(sql: str) -> ValidationResult:
    """
    Validate generated SQL for safety and correctness.

    Checks:
    1. SQL can be parsed (valid syntax)
    2. No destructive operations (UPDATE, DELETE, DROP, etc.)
    3. Only SELECT statements allowed
    4. Referenced tables exist in our schema

    Args:
        sql: The SQL string to validate.

    Returns:
        ValidationResult with is_valid flag and optional error message.
    """
    if not sql or not sql.strip():
        return ValidationResult(
            is_valid=False,
            sql=sql,
            error="Empty SQL query."
        )

    warnings = []

    # ── Step 1: Parse the SQL ──
    try:
        parsed = sqlglot.parse(sql, dialect="duckdb")
    except sqlglot.errors.ParseError as e:
        return ValidationResult(
            is_valid=False,
            sql=sql,
            error=f"SQL syntax error: {str(e)}"
        )

    if not parsed:
        return ValidationResult(
            is_valid=False,
            sql=sql,
            error="Failed to parse SQL — no statements found."
        )

    # ── Step 2: Check for destructive operations ──
    for statement in parsed:
        if statement is None:
            continue

        # Check the statement type
        for blocked_type in BLOCKED_STATEMENT_TYPES:
            if isinstance(statement, blocked_type):
                return ValidationResult(
                    is_valid=False,
                    sql=sql,
                    error=f"BLOCKED: {blocked_type.__name__} statements are not allowed. Only SELECT queries are permitted."
                )

        # Must be a SELECT (or a CTE with SELECT)
        if not isinstance(statement, exp.Select):
            # Check if it's a subquery or CTE that contains a SELECT
            has_select = any(
                isinstance(node, exp.Select)
                for node in statement.walk()
            )
            if not has_select:
                return ValidationResult(
                    is_valid=False,
                    sql=sql,
                    error=f"Only SELECT queries are allowed. Got: {type(statement).__name__}"
                )

    # ── Step 3: Verify referenced tables ──
    for statement in parsed:
        if statement is None:
            continue
        for table in statement.find_all(exp.Table):
            table_name = table.name.lower() if table.name else None
            if table_name and table_name not in VALID_TABLES:
                # Check if it's a CTE alias (not a real table)
                # CTEs appear as table references but are defined in WITH clauses
                cte_names = set()
                for cte in statement.find_all(exp.CTE):
                    if cte.alias:
                        cte_names.add(cte.alias.lower())

                if table_name not in cte_names:
                    warnings.append(
                        f"Table '{table_name}' not found in schema. "
                        f"Valid tables: {', '.join(sorted(VALID_TABLES))}"
                    )

    # If there are warnings about unknown tables, make it an error
    table_errors = [w for w in warnings if "not found in schema" in w]
    if table_errors:
        return ValidationResult(
            is_valid=False,
            sql=sql,
            error=table_errors[0],
            warnings=warnings
        )

    return ValidationResult(
        is_valid=True,
        sql=sql,
        warnings=warnings if warnings else None
    )


def sanitize_sql(sql: str) -> str:
    """
    Clean up SQL for execution. Removes comments, trims whitespace,
    ensures single semicolon termination.

    Args:
        sql: Raw SQL string.

    Returns:
        Sanitized SQL string.
    """
    # Remove SQL comments
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)

    # Trim and normalize whitespace
    sql = " ".join(sql.split())

    # Ensure single semicolon at end
    sql = sql.rstrip(";").strip() + ";"

    return sql
