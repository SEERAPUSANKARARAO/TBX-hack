"""
SQL Validator — Guardrails for Generated SQL
=============================================
Uses sqlglot to parse and validate SQL before execution.
Blocks destructive operations, verifies schema references, and blocks
direct selection of sensitive PII columns (account_number, utr_number)
— the client's data dictionary requires these never be shown raw.
"""
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp


VALID_TABLES = {
    "bank", "account", "transaction", "transaction_derived",
    "v_account_enriched", "v_transaction_enriched",
    "v_counterparty_spend_summary", "v_counterparty_lookup",
    "v_reconciliation_summary", "v_entity_lookup",
}

# Raw sensitive columns on the base tables. Never allowed in generated SQL —
# the enriched views expose masked_account_number / masked_utr_token instead.
SENSITIVE_COLUMNS = {"account_number", "utr_number"}

VALID_COLUMNS = {
    # bank
    "bank_code", "bank_name",
    # account
    "account_id", "entity_id", "program_id", "available_balance",
    # transaction
    "transaction_id", "transaction_date", "transaction_type", "description",
    "transaction_amount", "transaction_reference_id",
    # transaction_derived
    "rail_type", "counterparty_name", "counterparty_confidence",
    # view-only derived columns
    "masked_account_number", "masked_utr_token", "has_reference", "has_utr",
    "reconciliation_proxy_status", "txn_year", "txn_month", "txn_quarter",
    "txn_day_of_week", "total_spend", "avg_transaction", "min_transaction",
    "max_transaction", "transaction_count", "mention_count", "record_count",
    "total_amount", "account_count", "bank_count", "banks",
}

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
    """
    sql_block_pattern = r"```sql\s*\n?(.*?)```"
    matches = re.findall(sql_block_pattern, llm_response, re.DOTALL | re.IGNORECASE)
    if matches:
        return matches[0].strip()

    generic_block_pattern = r"```\s*\n?(.*?)```"
    matches = re.findall(generic_block_pattern, llm_response, re.DOTALL)
    if matches:
        return matches[0].strip()

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

    return llm_response.strip()


def validate_sql(sql: str) -> ValidationResult:
    """
    Validate generated SQL for safety and correctness.

    Checks:
    1. SQL can be parsed (valid syntax)
    2. No destructive operations (UPDATE, DELETE, DROP, etc.)
    3. Only SELECT statements allowed
    4. Referenced tables exist in our schema
    5. No raw selection of sensitive PII columns (account_number, utr_number)

    Args:
        sql: The SQL string to validate.

    Returns:
        ValidationResult with is_valid flag and optional error message.
    """
    if not sql or not sql.strip():
        return ValidationResult(is_valid=False, sql=sql, error="Empty SQL query.")

    warnings = []

    # ── Step 1: Parse ──
    try:
        parsed = sqlglot.parse(sql, dialect="mysql")
    except sqlglot.errors.ParseError as e:
        return ValidationResult(is_valid=False, sql=sql, error=f"SQL syntax error: {str(e)}")

    if not parsed:
        return ValidationResult(is_valid=False, sql=sql, error="Failed to parse SQL — no statements found.")

    # ── Step 2: Reject multi-statement SQL ──
    # Only the first statement would ever run (the read-only connection in
    # core/db_connection.py doesn't set CLIENT.MULTI_STATEMENTS), so silently
    # validating a whole batch would be misleading. Reject explicitly instead.
    statement_count = sum(1 for statement in parsed if statement is not None)
    if statement_count > 1:
        return ValidationResult(
            is_valid=False, sql=sql,
            error="BLOCKED: multiple SQL statements are not allowed. Submit exactly one SELECT statement.",
        )

    # ── Step 3: Block destructive operations, require SELECT ──
    for statement in parsed:
        if statement is None:
            continue

        for blocked_type in BLOCKED_STATEMENT_TYPES:
            if isinstance(statement, blocked_type):
                return ValidationResult(
                    is_valid=False, sql=sql,
                    error=f"BLOCKED: {blocked_type.__name__} statements are not allowed. Only SELECT queries are permitted."
                )

        if not isinstance(statement, exp.Select):
            has_select = any(isinstance(node, exp.Select) for node in statement.walk())
            if not has_select:
                return ValidationResult(
                    is_valid=False, sql=sql,
                    error=f"Only SELECT queries are allowed. Got: {type(statement).__name__}"
                )

    # ── Step 4: Verify referenced tables ──
    for statement in parsed:
        if statement is None:
            continue
        cte_names = {cte.alias.lower() for cte in statement.find_all(exp.CTE) if cte.alias}
        for table in statement.find_all(exp.Table):
            table_name = table.name.lower() if table.name else None
            if table_name and table_name not in VALID_TABLES and table_name not in cte_names:
                warnings.append(
                    f"Table '{table_name}' not found in schema. "
                    f"Valid tables: {', '.join(sorted(VALID_TABLES))}"
                )

    table_errors = [w for w in warnings if "not found in schema" in w]
    if table_errors:
        return ValidationResult(is_valid=False, sql=sql, error=table_errors[0], warnings=warnings)

    # ── Step 5: Block raw PII columns ──
    # Narrow and deliberate rather than a full column linter: false positives
    # here would break legitimate queries, but a PII leak is unacceptable, so
    # this specific check is a hard block regardless of alias/qualifier.
    for statement in parsed:
        if statement is None:
            continue
        for col in statement.find_all(exp.Column):
            col_name = col.name.lower() if col.name else None
            if col_name in SENSITIVE_COLUMNS:
                return ValidationResult(
                    is_valid=False, sql=sql,
                    error=(
                        f"BLOCKED: '{col_name}' is a sensitive column and must never be selected raw. "
                        f"Use masked_account_number (from v_account_enriched / v_transaction_enriched) or "
                        f"masked_utr_token (from v_transaction_enriched) instead."
                    ),
                )

    # Extra guard: `SELECT *` directly against `account` or `transaction`
    # (the raw base tables) could expose PII without ever naming the column.
    # Scoped to each select's OWN from/join targets (not `select.find_all`,
    # which would also recurse into a sibling WITH-clause's CTE bodies and
    # misattribute an unrelated CTE's tables to this select's star).
    for statement in parsed:
        if statement is None:
            continue
        for select in statement.find_all(exp.Select):
            selects_star = any(isinstance(e, exp.Star) for e in select.expressions)
            if not selects_star:
                continue
            scope_tables = []
            from_clause = select.args.get("from_")
            if from_clause:
                scope_tables.extend(from_clause.find_all(exp.Table))
            for join in select.args.get("joins") or []:
                scope_tables.extend(join.find_all(exp.Table))
            for table in scope_tables:
                if table.name and table.name.lower() in ("account", "transaction"):
                    return ValidationResult(
                        is_valid=False, sql=sql,
                        error=(
                            f"BLOCKED: 'SELECT *' against the raw '{table.name}' table can expose sensitive "
                            f"columns. Select explicit columns, or query v_account_enriched / "
                            f"v_transaction_enriched instead."
                        ),
                    )

    return ValidationResult(is_valid=True, sql=sql, warnings=warnings if warnings else None)


def validate_entity_scope(sql: str, entity_id: str | None) -> ValidationResult:
    """
    When a customer entity_id is selected in the UI (see api/models.py's
    QueryRequest.entity_id), require every generated query to filter by it.
    This is a usability scope, not a security boundary — the read-only DB
    user can still read every entity's rows (see core/db_connection.py) —
    but it stops the LLM from silently returning another customer's data
    when one was explicitly selected.

    A plain substring check, not a parsed-SQL check: deliberately simple so
    it stays predictable for auto-repair prompting. `entity_id=None` means
    no customer is selected, so there's nothing to scope — always valid.
    """
    if not entity_id:
        return ValidationResult(is_valid=True, sql=sql)

    if entity_id not in sql:
        return ValidationResult(
            is_valid=False, sql=sql,
            error=(
                f"BLOCKED: a customer is selected (entity_id='{entity_id}') but the query doesn't "
                f"filter by it. Every account/transaction reference must be scoped to this entity_id "
                f"— join to account and add `account.entity_id = '{entity_id}'` (or filter directly "
                f"on the enriched views' entity_id column)."
            ),
        )

    return ValidationResult(is_valid=True, sql=sql)


def enforce_row_limit(sql: str, max_rows: int = 1000) -> str:
    """
    Ensure the top-level SELECT has a LIMIT, so a broad/unfiltered query
    can't return the whole table into the LLM narration step. Assumes
    `sql` has already passed validate_sql.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect="mysql")
    except sqlglot.errors.ParseError:
        return sql

    if isinstance(tree, exp.Select) and not tree.args.get("limit"):
        tree = tree.limit(max_rows)
        return tree.sql(dialect="mysql") + ";"

    return sql


def sanitize_sql(sql: str) -> str:
    """
    Clean up SQL for execution. Removes comments, trims whitespace,
    ensures single semicolon termination.
    """
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = " ".join(sql.split())
    sql = sql.rstrip(";").strip() + ";"
    return sql
