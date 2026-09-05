"""
Tests for core/sql_validator.py guardrails.
"""
import pytest

from core.sql_validator import (
    validate_sql, validate_entity_scope, enforce_row_limit, sanitize_sql,
)


def test_valid_select_on_allowed_view_passes():
    result = validate_sql("SELECT * FROM v_transaction_enriched LIMIT 10;")
    assert result.is_valid


@pytest.mark.parametrize("sql", [
    "UPDATE account SET available_balance = 0;",
    "DELETE FROM transaction;",
    "DROP TABLE account;",
    "INSERT INTO account (account_id) VALUES ('x');",
    "ALTER TABLE account ADD COLUMN foo INT;",
    "CREATE TABLE evil (id INT);",
])
def test_blocked_statement_types_are_rejected(sql):
    result = validate_sql(sql)
    assert not result.is_valid
    assert "BLOCKED" in result.error


@pytest.mark.parametrize("sql", [
    "SELECT account_number FROM account;",
    "SELECT a.account_number FROM account a;",
    "SELECT utr_number FROM transaction;",
])
def test_raw_pii_columns_are_rejected(sql):
    result = validate_sql(sql)
    assert not result.is_valid
    assert "sensitive column" in result.error


def test_select_star_against_raw_account_table_is_rejected():
    result = validate_sql("SELECT * FROM account;")
    assert not result.is_valid
    assert "SELECT *" in result.error


def test_select_star_against_raw_transaction_table_is_rejected():
    result = validate_sql("SELECT * FROM transaction;")
    assert not result.is_valid
    assert "SELECT *" in result.error


def test_select_star_against_enriched_view_passes():
    result = validate_sql("SELECT * FROM v_account_enriched;")
    assert result.is_valid


def test_unknown_table_is_rejected():
    result = validate_sql("SELECT * FROM not_a_real_table;")
    assert not result.is_valid
    assert "not found in schema" in result.error


def test_multi_statement_sql_is_rejected():
    result = validate_sql("SELECT 1; SELECT 2;")
    assert not result.is_valid
    assert "multiple SQL statements" in result.error


def test_enforce_row_limit_adds_limit_when_missing():
    sql = enforce_row_limit("SELECT * FROM v_account_enriched;")
    assert "LIMIT" in sql.upper()


def test_enforce_row_limit_leaves_existing_limit_untouched():
    sql = enforce_row_limit("SELECT * FROM v_account_enriched LIMIT 5;", max_rows=1000)
    assert sql.upper().count("LIMIT") == 1
    assert "5" in sql


def test_sanitize_sql_strips_line_comments():
    sql = sanitize_sql("SELECT 1 -- this is a comment\n;")
    assert "comment" not in sql


def test_sanitize_sql_strips_block_comments():
    sql = sanitize_sql("SELECT /* block comment */ 1;")
    assert "block comment" not in sql


def test_sanitize_sql_normalizes_trailing_semicolon():
    sql = sanitize_sql("SELECT 1;;;")
    assert sql.endswith("1;")
    assert sql.count(";") == 1


def test_validate_entity_scope_passes_when_entity_id_present():
    result = validate_entity_scope("SELECT * FROM account WHERE entity_id = 'E123';", "E123")
    assert result.is_valid


def test_validate_entity_scope_fails_when_entity_id_missing():
    result = validate_entity_scope("SELECT * FROM account;", "E123")
    assert not result.is_valid
    assert "BLOCKED" in result.error
    assert "E123" in result.error


def test_validate_entity_scope_passes_when_no_entity_selected():
    result = validate_entity_scope("SELECT * FROM account;", None)
    assert result.is_valid
