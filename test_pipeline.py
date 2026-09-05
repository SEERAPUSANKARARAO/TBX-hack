"""
Test Pipeline — End-to-End Validation
======================================
Interactive test script to validate each component of the
Text-to-SQL pipeline, from entity resolution to SQL execution.

Usage:
    python test_pipeline.py              # Run all tests
    python test_pipeline.py --dry-run    # Skip LLM, show prompts only
    python test_pipeline.py --interactive  # Interactive query mode
"""
import sys
import argparse
from pathlib import Path
from datetime import date

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    DUCKDB_PATH, LLM_PROVIDER,
    OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL, GROQ_API_KEY, GROQ_MODEL,
    MAX_SQL_RETRIES, FUZZY_MATCH_THRESHOLD, LLM_TEMPERATURE, LLM_MAX_TOKENS,
)
from core.data_bounds import get_reference_date

DIVIDER = "=" * 70
SUBDIV = "-" * 50

TEST_QUERIES = [
    "How much did we send to Amazon Retail India this year?",
    "Show me all unreconciled transactions",
    "What's our available balance at HDFC Bank?",
    "Which counterparties have the most transactions?",
    "Compare our spend to Bharti Airtel Limited month over month",
]


def test_description_parser():
    """Test the description parser against known real narration formats."""
    from core.description_parser import parse_description

    print(f"\n{DIVIDER}")
    print("TEST: Description Parser (real narration samples)")
    print(DIVIDER)

    cases = [
        ("FT -  95842568 -  50200013729069 - SELECTION ELECTRONICS   DAHISAR EAST", "SELECTION ELECTRONICS"),
        ("UPI-NAVYUG SELECTION-XXXXXX8672-AUBL0002125-103293775381-260514201735136", "NAVYUG SELECTION"),
        ("NEFT/000483399203/ICIC/PARESH VIKRANT GHASE", "PARESH VIKRANT GHASE"),
        ("IMPS OW/507614422198/Gautam singh/SBIN/43292707719", "GAUTAM SINGH"),
        ("NEFT  - ICIC0001241 - 95584112 - 124105002702 - SELECTION MOBILE", "SELECTION MOBILE"),
    ]

    all_passed = True
    for description, expected_contains in cases:
        result = parse_description(description)
        name = result["counterparty_name"] or ""
        passed = expected_contains.upper() in name
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"  [{status}] {description[:55]}...")
        print(f"        -> rail={result['rail_type']} name={name!r} confidence={result['confidence']}")

    return all_passed


def test_entity_resolver():
    """Test the entity resolver component in isolation."""
    from core.entity_resolver import EntityResolver

    print(f"\n{DIVIDER}")
    print("TEST: Entity Resolver")
    print(DIVIDER)

    resolver = EntityResolver(DUCKDB_PATH, FUZZY_MATCH_THRESHOLD)
    print(f"  Loaded {len(resolver.counterparties)} counterparties from database\n")
    reference_date = get_reference_date(DUCKDB_PATH)

    test_cases = [
        ("How much did we send to Amazon Retail India?", "Should match Amazon Retail India"),
        ("Selection Electronics spending in March", "Should match Selection Electronics + resolve March"),
        ("Show Gautam Singh payments", "Should match Gautam Singh"),
        ("What about Bharti Airtel last quarter?", "Should match + last quarter"),
        ("Spending on Quantum Retail Ltd", "Should flag as unresolved"),
        ("Total expenses in Q2 2026", "Should resolve Q2 dates"),
        ("Costs last month", "Should resolve relative date"),
        ("Which transactions may be unreconciled", "Should NOT misread 'may' as the month"),
    ]

    all_passed = True
    for query, expected in test_cases:
        result = resolver.resolve(query, reference_date=reference_date)
        status = "PASS" if (result.counterparty_name or result.unresolved_counterparty or result.start_date or "may" in query.lower()) else "WARN"
        if status == "WARN":
            all_passed = False

        print(f"  [{status}] \"{query}\"")
        print(f"        Expected: {expected}")
        if result.counterparty_name:
            print(f"        Counterparty: {result.counterparty_name} ({result.counterparty_match_score:.0f}% match)")
        if result.unresolved_counterparty:
            print(f"        Unresolved: '{result.unresolved_counterparty}'")
        if result.start_date:
            print(f"        Dates:    {result.start_date} to {result.end_date} ({result.date_expression})")
        else:
            print(f"        Dates:    none resolved")
        print()

    return all_passed


def test_sql_validator():
    """Test the SQL validator component in isolation."""
    from core.sql_validator import validate_sql, extract_sql_from_response

    print(f"\n{DIVIDER}")
    print("TEST: SQL Validator & Guardrails")
    print(DIVIDER)

    test_cases = [
        ("SELECT * FROM v_transaction_enriched LIMIT 10;", True, "Valid simple SELECT against a view"),
        ("SELECT SUM(transaction_amount) FROM transaction WHERE transaction_type = 'debit';", True, "Valid aggregation"),
        ("WITH cte AS (SELECT transaction_id, transaction_amount FROM transaction) SELECT * FROM cte;", True, "Valid CTE (explicit non-PII columns)"),
        ("WITH cte AS (SELECT * FROM transaction) SELECT * FROM cte;", False, "BLOCKED: CTE wrapping SELECT * still exposes utr_number"),
        ("DROP TABLE transaction;", False, "BLOCKED: DROP statement"),
        ("DELETE FROM transaction WHERE 1=1;", False, "BLOCKED: DELETE statement"),
        ("UPDATE transaction SET transaction_amount = 0;", False, "BLOCKED: UPDATE statement"),
        ("INSERT INTO transaction (transaction_id) VALUES ('x');", False, "BLOCKED: INSERT"),
        ("SELECT * FROM nonexistent_table;", False, "Invalid: unknown table"),
        ("", False, "Invalid: empty SQL"),
        ("SELECT account_number FROM account;", False, "BLOCKED: raw PII column (account_number)"),
        ("SELECT utr_number FROM transaction;", False, "BLOCKED: raw PII column (utr_number)"),
        ("SELECT * FROM account;", False, "BLOCKED: SELECT * against raw account table"),
        ("SELECT masked_account_number FROM v_account_enriched;", True, "Valid: masked column via view"),
    ]

    all_passed = True
    for sql, expected_valid, desc in test_cases:
        result = validate_sql(sql)
        passed = result.is_valid == expected_valid
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False

        print(f"  [{status}] {desc}")
        if not passed:
            print(f"        Expected valid={expected_valid}, got valid={result.is_valid}")
            if result.error:
                print(f"        Error: {result.error}")
        print()

    print(f"  {SUBDIV}")
    print("  SQL Extraction from LLM responses:\n")

    extraction_tests = [
        ("Here is the SQL:\n```sql\nSELECT * FROM transaction;\n```\nThis query...",
         "SELECT * FROM transaction;", "Extract from ```sql block"),
        ("```\nSELECT COUNT(*) FROM transaction;\n```",
         "SELECT COUNT(*) FROM transaction;", "Extract from generic ``` block"),
        ("SELECT counterparty_name, SUM(transaction_amount) FROM v_transaction_enriched GROUP BY counterparty_name;",
         "SELECT counterparty_name, SUM(transaction_amount) FROM v_transaction_enriched GROUP BY counterparty_name;",
         "Raw SQL (no markdown)"),
    ]

    for response, expected_sql, desc in extraction_tests:
        extracted = extract_sql_from_response(response)
        passed = expected_sql in extracted
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"  [{status}] {desc}")
        if not passed:
            print(f"        Expected: {expected_sql}")
            print(f"        Got:      {extracted}")
        print()

    return all_passed


def test_query_engine():
    """Test the query engine component in isolation."""
    from core.query_engine import QueryEngine

    print(f"\n{DIVIDER}")
    print("TEST: Query Engine (DuckDB)")
    print(DIVIDER)

    engine = QueryEngine(DUCKDB_PATH)

    test_queries = [
        ("SELECT COUNT(*) AS total FROM transaction;", "Count transactions"),
        ("SELECT SUM(transaction_amount) AS total_spend FROM transaction WHERE transaction_type='debit';", "Total spend"),
        (
            "SELECT counterparty_name, SUM(transaction_amount) AS total "
            "FROM v_transaction_enriched WHERE transaction_type='debit' AND counterparty_name IS NOT NULL "
            "GROUP BY counterparty_name ORDER BY total DESC LIMIT 3;",
            "Top 3 counterparties"
        ),
        (
            "SELECT reconciliation_proxy_status, COUNT(*) AS cnt "
            "FROM v_transaction_enriched GROUP BY reconciliation_proxy_status ORDER BY cnt DESC;",
            "Reconciliation-proxy breakdown"
        ),
        (
            "SELECT * FROM v_counterparty_spend_summary "
            "WHERE counterparty_name ILIKE '%amazon%' ORDER BY txn_year, txn_month;",
            "Amazon monthly spend (using view)"
        ),
    ]

    all_passed = True
    for sql, desc in test_queries:
        result = engine.execute(sql)
        status = "PASS" if result.success else "FAIL"
        if not result.success:
            all_passed = False

        print(f"\n  [{status}] {desc}")
        print(f"  SQL: {sql[:80]}...")
        if result.success:
            print(f"  Rows: {result.row_count}, Time: {result.execution_time_ms:.1f}ms")
            if result.rows:
                row_dict = dict(zip(result.columns, result.rows[0]))
                print(f"  First row: {row_dict}")
        else:
            print(f"  Error: {result.error}")

    return all_passed


def _build_generator():
    from core.sql_generator import SQLGenerator

    model = {
        "openrouter": OPENROUTER_MODEL, "ollama": OLLAMA_MODEL,
        "openai": OPENAI_MODEL, "groq": GROQ_MODEL,
    }.get(LLM_PROVIDER, OPENROUTER_MODEL)

    return SQLGenerator(
        db_path=DUCKDB_PATH, llm_provider=LLM_PROVIDER, llm_model=model,
        ollama_base_url=OLLAMA_BASE_URL, openrouter_api_key=OPENROUTER_API_KEY,
        openrouter_base_url=OPENROUTER_BASE_URL, openai_api_key=OPENAI_API_KEY,
        groq_api_key=GROQ_API_KEY, max_retries=MAX_SQL_RETRIES,
        fuzzy_threshold=FUZZY_MATCH_THRESHOLD, temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
    ), model


def test_full_pipeline(dry_run: bool = False):
    """Test the full Text-to-SQL pipeline."""
    print(f"\n{DIVIDER}")
    print(f"TEST: Full Pipeline {'(DRY RUN)' if dry_run else '(LIVE)'}")
    print(DIVIDER)

    generator, model = _build_generator()
    reference_date = get_reference_date(DUCKDB_PATH)
    print(f"  Reference date (data max, not wall-clock): {reference_date}\n")

    for i, query in enumerate(TEST_QUERIES, 1):
        print(f"\n  {SUBDIV}")
        print(f"  Query {i}: \"{query}\"")
        print(f"  {SUBDIV}")

        result = generator.generate(query, reference_date=reference_date, dry_run=dry_run)

        if dry_run:
            print(f"  [DRY RUN] Entity resolution: {result.resolved_entities}")
            print(f"  [DRY RUN] Assembled prompt length: {len(result.prompt)} chars")
            continue

        print(f"  SQL: {result.extracted_sql}")
        print(f"  Valid: {result.sql_valid}")
        if result.validation_error:
            print(f"  Validation Error: {result.validation_error}")
        if result.clarification_needed:
            print(f"  Clarification: {result.clarification_needed}")
        if result.query_result and result.query_result.success:
            print(f"  Execution Time: {result.query_result.execution_time_ms:.1f}ms")
            print(f"  Tables: {result.query_result.tables_touched}")
            print(f"  Rows: {result.query_result.row_count}")
        elif result.error:
            print(f"  Pipeline Error: {result.error}")
        print(f"  Total Time: {result.total_time_ms:.1f}ms (retries: {result.retries}, "
              f"tokens: {result.prompt_tokens}/{result.completion_tokens})")


def interactive_mode(dry_run: bool = False):
    """Interactive query mode — type questions and get SQL + results."""
    generator, model = _build_generator()
    reference_date = get_reference_date(DUCKDB_PATH)

    print(f"\n{DIVIDER}")
    print("Financial AI Chatbot — Interactive Mode")
    print(f"Provider: {LLM_PROVIDER}, Model: {model}")
    print(f"Data reference date: {reference_date}")
    print(f"{'(DRY RUN)' if dry_run else ''}")
    print("Type 'quit' or 'exit' to stop. Type 'history' to see conversation.")
    print(DIVIDER)

    while True:
        try:
            query = input("\n  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye!")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("  Goodbye!")
            break
        if query.lower() == "history":
            for turn in generator.conversation_history:
                print(f"\n  Q: {turn['query']}")
                print(f"  SQL: {turn['sql'][:100]}...")
            continue

        result = generator.generate(query, reference_date=reference_date, dry_run=dry_run)

        if result.clarification_needed:
            print(f"\n  Bot: {result.clarification_needed}")
            continue
        if result.error:
            print(f"\n  Error: {result.error}")
            continue

        if result.extracted_sql and not dry_run:
            print("\n  SQL:")
            for line in result.extracted_sql.split("\n"):
                print(f"    {line}")

        if result.query_result and result.query_result.success:
            print(f"\n  {result.query_result.row_count} row(s) in {result.query_result.execution_time_ms:.1f}ms")
        elif result.validation_error:
            print(f"\n  Validation failed: {result.validation_error}")

        print(f"\n  [{result.total_time_ms:.0f}ms, retries: {result.retries}, "
              f"tokens: {result.prompt_tokens}/{result.completion_tokens}]")


def main():
    parser = argparse.ArgumentParser(description="Test the Financial AI Chatbot pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls, show assembled prompts only")
    parser.add_argument("--interactive", "-i", action="store_true", help="Enter interactive query mode")
    parser.add_argument("--component", choices=["parser", "resolver", "validator", "engine", "pipeline"],
                        help="Test a specific component only")
    args = parser.parse_args()

    print(f"\n{DIVIDER}")
    print("Financial AI Chatbot — Test Suite")
    print(f"Database: {DUCKDB_PATH}")
    print(DIVIDER)

    if args.interactive:
        interactive_mode(dry_run=args.dry_run)
        return

    results = {}

    if not args.component or args.component == "parser":
        results["Description Parser"] = test_description_parser()

    if not args.component or args.component == "resolver":
        results["Entity Resolver"] = test_entity_resolver()

    if not args.component or args.component == "validator":
        results["SQL Validator"] = test_sql_validator()

    if not args.component or args.component == "engine":
        results["Query Engine"] = test_query_engine()

    if not args.component or args.component == "pipeline":
        test_full_pipeline(dry_run=args.dry_run)

    if results:
        print(f"\n\n{DIVIDER}")
        print("TEST SUMMARY")
        print(DIVIDER)
        for name, passed in results.items():
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name}")
        print()


if __name__ == "__main__":
    main()
