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
import os
import argparse
from pathlib import Path
from datetime import date

# Force UTF-8 output on Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    DUCKDB_PATH, LLM_PROVIDER,
    OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL, GROQ_API_KEY, GROQ_MODEL,
    MAX_SQL_RETRIES, FUZZY_MATCH_THRESHOLD, LLM_TEMPERATURE, LLM_MAX_TOKENS,
)


DIVIDER = "=" * 70
SUBDIV = "-" * 50

# ── Test queries ──
TEST_QUERIES = [
    "How much did we spend on AWS this year?",
    "Show me all unreconciled transactions",
    "What's Wayne Enterprises' total consulting spend?",
    "Which vendors have pending payouts?",
    "Compare our software subscription costs month over month",
]


def test_entity_resolver():
    """Test the entity resolver component in isolation."""
    from core.entity_resolver import EntityResolver

    print(f"\n{DIVIDER}")
    print("TEST: Entity Resolver")
    print(DIVIDER)

    resolver = EntityResolver(DUCKDB_PATH, FUZZY_MATCH_THRESHOLD)
    print(f"  Loaded {len(resolver.vendors)} vendors from database\n")

    test_cases = [
        ("How much did we spend on AWS?", "Should match Amazon Web Services Inc."),
        ("Acme Corp spending in March", "Should match Acme Corp + resolve March"),
        ("Show Dunder Mifflin orders", "Should match Dunder Mifflin Paper Co."),
        ("What about Stark Industries last quarter?", "Should match + last quarter"),
        ("Spending on XYZ Unknown Corp", "Should flag as unresolved"),
        ("Total expenses in Q2 2026", "Should resolve Q2 dates"),
        ("Costs last month", "Should resolve relative date"),
    ]

    all_passed = True
    for query, expected in test_cases:
        result = resolver.resolve(query, reference_date=date(2026, 7, 1))
        status = "PASS" if (result.vendor_name or result.unresolved_vendor or result.start_date) else "WARN"
        if status == "WARN":
            all_passed = False

        print(f"  [{status}] \"{query}\"")
        print(f"        Expected: {expected}")
        if result.vendor_name:
            print(f"        Vendor:   {result.vendor_name} (id={result.vendor_id})")
        if result.unresolved_vendor:
            print(f"        Unresolved: '{result.unresolved_vendor}'")
        if result.start_date:
            print(f"        Dates:    {result.start_date} to {result.end_date} ({result.date_expression})")
        print()

    return all_passed


def test_sql_validator():
    """Test the SQL validator component in isolation."""
    from core.sql_validator import validate_sql, extract_sql_from_response

    print(f"\n{DIVIDER}")
    print("TEST: SQL Validator & Guardrails")
    print(DIVIDER)

    test_cases = [
        # (sql, should_be_valid, description)
        ("SELECT * FROM transactions LIMIT 10;", True, "Valid simple SELECT"),
        ("SELECT SUM(amount) FROM transactions WHERE vendor_name = 'AWS';", True, "Valid aggregation"),
        (
            "WITH cte AS (SELECT * FROM transactions) SELECT * FROM cte;",
            True, "Valid CTE"
        ),
        ("DROP TABLE transactions;", False, "BLOCKED: DROP statement"),
        ("DELETE FROM transactions WHERE 1=1;", False, "BLOCKED: DELETE statement"),
        ("UPDATE transactions SET amount = 0;", False, "BLOCKED: UPDATE statement"),
        ("INSERT INTO transactions VALUES (999, '2026-01-01', NULL, 1, 'Test', 100, 'USD', 'debit', '5200', 'Test', 'Test', 'REF', 'ACH');", False, "BLOCKED: INSERT"),
        ("SELECT * FROM nonexistent_table;", False, "Invalid: unknown table"),
        ("", False, "Invalid: empty SQL"),
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

    # Test SQL extraction from LLM response
    print(f"  {SUBDIV}")
    print("  SQL Extraction from LLM responses:\n")

    extraction_tests = [
        (
            "Here is the SQL:\n```sql\nSELECT * FROM transactions;\n```\nThis query...",
            "SELECT * FROM transactions;",
            "Extract from ```sql block"
        ),
        (
            "```\nSELECT COUNT(*) FROM vendor_payouts;\n```",
            "SELECT COUNT(*) FROM vendor_payouts;",
            "Extract from generic ``` block"
        ),
        (
            "SELECT vendor_name, SUM(amount) FROM transactions GROUP BY vendor_name;",
            "SELECT vendor_name, SUM(amount) FROM transactions GROUP BY vendor_name;",
            "Raw SQL (no markdown)"
        ),
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
        ("SELECT COUNT(*) AS total FROM transactions;", "Count transactions"),
        ("SELECT SUM(amount) AS total_spend FROM transactions;", "Total spend"),
        (
            "SELECT vendor_name, SUM(amount) AS total "
            "FROM transactions GROUP BY vendor_name "
            "ORDER BY total DESC LIMIT 3;",
            "Top 3 vendors"
        ),
        (
            "SELECT status, COUNT(*) AS cnt "
            "FROM reconciliation_status GROUP BY status "
            "ORDER BY cnt DESC;",
            "Reconciliation breakdown"
        ),
        (
            "SELECT * FROM v_vendor_spend_summary "
            "WHERE vendor_name = 'Amazon Web Services Inc.' "
            "ORDER BY spend_year, spend_month;",
            "AWS monthly spend (using view)"
        ),
    ]

    all_passed = True
    for sql, desc in test_queries:
        result = engine.execute(sql)
        status = "PASS" if result.success else "FAIL"
        if not status:
            all_passed = False

        print(f"\n  [{status}] {desc}")
        print(f"  SQL: {sql[:80]}...")
        if result.success:
            print(f"  Rows: {result.row_count}, Time: {result.execution_time_ms:.1f}ms")
            if result.rows:
                # Show first row
                row_dict = dict(zip(result.columns, result.rows[0]))
                print(f"  First row: {row_dict}")
        else:
            print(f"  Error: {result.error}")

    return all_passed


def test_full_pipeline(dry_run: bool = False):
    """Test the full Text-to-SQL pipeline."""
    from core.sql_generator import SQLGenerator

    print(f"\n{DIVIDER}")
    print(f"TEST: Full Pipeline {'(DRY RUN)' if dry_run else '(LIVE)'}")
    print(DIVIDER)

    # Determine which LLM model to use based on provider
    model = {
        "openrouter": OPENROUTER_MODEL,
        "ollama": OLLAMA_MODEL,
        "openai": OPENAI_MODEL,
        "groq": GROQ_MODEL,
    }.get(LLM_PROVIDER, OPENROUTER_MODEL)

    generator = SQLGenerator(
        db_path=DUCKDB_PATH,
        llm_provider=LLM_PROVIDER,
        llm_model=model,
        ollama_base_url=OLLAMA_BASE_URL,
        openrouter_api_key=OPENROUTER_API_KEY,
        openrouter_base_url=OPENROUTER_BASE_URL,
        openai_api_key=OPENAI_API_KEY,
        groq_api_key=GROQ_API_KEY,
        max_retries=MAX_SQL_RETRIES,
        fuzzy_threshold=FUZZY_MATCH_THRESHOLD,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
    )

    for i, query in enumerate(TEST_QUERIES, 1):
        print(f"\n  {SUBDIV}")
        print(f"  Query {i}: \"{query}\"")
        print(f"  {SUBDIV}")

        result = generator.generate(query, reference_date=date(2026, 7, 1), dry_run=dry_run)

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
            print(f"  Summary: {result.query_result.summary_text()}")
        elif result.error:
            print(f"  Pipeline Error: {result.error}")
        print(f"  Total Time: {result.total_time_ms:.1f}ms (retries: {result.retries})")


def interactive_mode(dry_run: bool = False):
    """Interactive query mode — type questions and get SQL + results."""
    from core.sql_generator import SQLGenerator

    model = {
        "openrouter": OPENROUTER_MODEL,
        "ollama": OLLAMA_MODEL,
        "openai": OPENAI_MODEL,
        "groq": GROQ_MODEL,
    }.get(LLM_PROVIDER, OPENROUTER_MODEL)

    generator = SQLGenerator(
        db_path=DUCKDB_PATH,
        llm_provider=LLM_PROVIDER,
        llm_model=model,
        ollama_base_url=OLLAMA_BASE_URL,
        openrouter_api_key=OPENROUTER_API_KEY,
        openrouter_base_url=OPENROUTER_BASE_URL,
        openai_api_key=OPENAI_API_KEY,
        groq_api_key=GROQ_API_KEY,
        max_retries=MAX_SQL_RETRIES,
        fuzzy_threshold=FUZZY_MATCH_THRESHOLD,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
    )

    print(f"\n{DIVIDER}")
    print("Financial AI Chatbot — Interactive Mode")
    print(f"Provider: {LLM_PROVIDER}, Model: {model}")
    print(f"{'(DRY RUN)' if dry_run else ''}")
    print(f"Type 'quit' or 'exit' to stop. Type 'history' to see conversation.")
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

        result = generator.generate(query, dry_run=dry_run)

        if result.clarification_needed:
            print(f"\n  Bot: {result.clarification_needed}")
            continue

        if result.error:
            print(f"\n  Error: {result.error}")
            continue

        if result.extracted_sql and not dry_run:
            print(f"\n  SQL:")
            for line in result.extracted_sql.split("\n"):
                print(f"    {line}")

        if result.query_result and result.query_result.success:
            print(f"\n{result.query_result.summary_text()}")
        elif result.validation_error:
            print(f"\n  Validation failed: {result.validation_error}")

        print(f"\n  [{result.total_time_ms:.0f}ms, retries: {result.retries}]")


def main():
    parser = argparse.ArgumentParser(description="Test the Financial AI Chatbot pipeline")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip LLM calls, show assembled prompts only")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Enter interactive query mode")
    parser.add_argument("--component", choices=["resolver", "validator", "engine", "pipeline"],
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

    if not args.component or args.component == "resolver":
        results["Entity Resolver"] = test_entity_resolver()

    if not args.component or args.component == "validator":
        results["SQL Validator"] = test_sql_validator()

    if not args.component or args.component == "engine":
        results["Query Engine"] = test_query_engine()

    if not args.component or args.component == "pipeline":
        test_full_pipeline(dry_run=args.dry_run)

    # Summary
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
