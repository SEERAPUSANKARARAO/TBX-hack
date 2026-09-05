"""
Database Initialization Script
==============================
Ingests bank/account/transaction CSVs into DuckDB, creates schema
(tables + views), runs the description parser to populate
transaction_derived, and prints summary statistics.

Usage:
    python db/init_db.py
"""
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb
from config import DUCKDB_PATH, SAMPLE_DATA_DIR, CSV_TO_TABLE_MAP
from core.description_parser import parse_description

DB_DIR = Path(DUCKDB_PATH).parent
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_database():
    """Create the DuckDB database, load schema, ingest CSVs, derive counterparties."""

    DB_DIR.mkdir(parents=True, exist_ok=True)

    db_path = Path(DUCKDB_PATH)
    if db_path.exists():
        db_path.unlink()
        print(f"Removed existing database: {db_path}")

    con = duckdb.connect(str(db_path))
    print(f"Connected to DuckDB: {db_path}")

    # ── Step 1: Schema DDL ──
    print("\nCreating schema (tables + views)...")
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    con.execute(schema_sql)
    print("   Schema created successfully")

    # ── Step 2: Ingest CSV files (bank, account, transaction) ──
    print("\nIngesting CSV data...")
    for csv_file, table_name in CSV_TO_TABLE_MAP.items():
        csv_path = SAMPLE_DATA_DIR / csv_file
        if not csv_path.exists():
            print(f"   Skipping {csv_file} (file not found)")
            continue

        con.execute(f"""
            INSERT INTO {table_name}
            SELECT * FROM read_csv_auto('{csv_path.as_posix()}',
                                        header=true,
                                        ignore_errors=true)
        """)
        row_count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"   {table_name}: {row_count} rows loaded from {csv_file}")

    # ── Step 3: Derive counterparty/rail info for every transaction ──
    # Deterministic Python parsing (core/description_parser.py), never an
    # LLM call — run once here so SQL generation queries a plain column.
    print("\nRunning description parser to populate transaction_derived...")
    txns = con.execute("SELECT transaction_id, description FROM transaction").fetchall()
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    derived_rows = []
    for txn_id, description in txns:
        parsed = parse_description(description)
        confidence_counts[parsed["confidence"]] += 1
        derived_rows.append((txn_id, parsed["rail_type"], parsed["counterparty_name"], parsed["confidence"]))

    con.executemany(
        "INSERT INTO transaction_derived (transaction_id, rail_type, counterparty_name, counterparty_confidence) "
        "VALUES (?, ?, ?, ?)",
        derived_rows,
    )
    total = len(derived_rows)
    print(f"   Parsed {total} descriptions:")
    for level in ("high", "medium", "low"):
        pct = (confidence_counts[level] / total * 100) if total else 0
        print(f"     {level:6}: {confidence_counts[level]:4} ({pct:.1f}%)")
    print(f"   (low-confidence rows have counterparty_name=NULL and are excluded from")
    print(f"    v_counterparty_lookup / v_counterparty_spend_summary — the raw description")
    print(f"    is always shown alongside so nothing is silently guessed.)")

    # ── Step 4: Summary statistics ──
    print("\n" + "=" * 60)
    print("DATABASE SUMMARY")
    print("=" * 60)

    tables = con.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """).fetchall()

    print(f"\n{'Table':<30} {'Rows':>10}")
    print("-" * 42)
    total_rows = 0
    for (table_name,) in tables:
        count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        total_rows += count
        print(f"  {table_name:<28} {count:>10,}")
    print("-" * 42)
    print(f"  {'TOTAL':<28} {total_rows:>10,}")

    views = con.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' AND table_type = 'VIEW'
        ORDER BY table_name
    """).fetchall()
    print(f"\nViews created: {len(views)}")
    for (view_name,) in views:
        print(f"   - {view_name}")

    # ── Step 5: Validation queries ──
    print("\n" + "=" * 60)
    print("VALIDATION QUERIES")
    print("=" * 60)

    date_range = con.execute("SELECT MIN(transaction_date), MAX(transaction_date) FROM transaction").fetchone()
    print(f"\n  Transaction date range: {date_range[0]} to {date_range[1]}")

    total_debit = con.execute("SELECT SUM(transaction_amount) FROM transaction WHERE transaction_type='debit'").fetchone()[0]
    total_credit = con.execute("SELECT SUM(transaction_amount) FROM transaction WHERE transaction_type='credit'").fetchone()[0]
    print(f"  Total debits:  {total_debit:,.2f}")
    print(f"  Total credits: {total_credit:,.2f}")

    print("\n  Top 5 counterparties by spend (debits, high/medium confidence only):")
    top = con.execute("""
        SELECT counterparty_name, SUM(transaction_amount) AS total
        FROM v_transaction_enriched
        WHERE transaction_type = 'debit' AND counterparty_name IS NOT NULL
        GROUP BY counterparty_name ORDER BY total DESC LIMIT 5
    """).fetchall()
    for name, total in top:
        print(f"    - {name}: {total:,.2f}")

    print("\n  Reconciliation-proxy breakdown (has_reference OR has_utr):")
    recon = con.execute("SELECT * FROM v_reconciliation_summary ORDER BY record_count DESC").fetchall()
    for status, cnt, amt in recon:
        print(f"    - {status}: {cnt} rows, {amt:,.2f} total")

    print("\n  Potential anomalies (>2.5 sigma above counterparty mean, debits):")
    anomalies = con.execute("""
        WITH stats AS (
            SELECT counterparty_name, AVG(transaction_amount) AS avg_amt, STDDEV(transaction_amount) AS std_amt
            FROM v_transaction_enriched
            WHERE transaction_type = 'debit' AND counterparty_name IS NOT NULL
            GROUP BY counterparty_name HAVING COUNT(*) > 1
        )
        SELECT t.transaction_id, t.counterparty_name, t.transaction_amount, s.avg_amt, s.std_amt
        FROM v_transaction_enriched t
        JOIN stats s ON t.counterparty_name = s.counterparty_name
        WHERE s.std_amt > 0 AND t.transaction_amount > s.avg_amt + 2.5 * s.std_amt
    """).fetchall()
    if anomalies:
        for txn_id, name, amount, avg, std in anomalies:
            amount, avg = float(amount), float(avg)
            pct = ((amount - avg) / avg) * 100
            print(f"    - {txn_id[:8]}...: {amount:,.2f} to {name} ({pct:.0f}% above avg {avg:,.2f})")
    else:
        print("    None detected")

    print("\n  Sanity check — masked columns never expose raw PII:")
    sample = con.execute("SELECT masked_account_number, masked_utr_token FROM v_transaction_enriched WHERE masked_utr_token IS NOT NULL LIMIT 1").fetchone()
    print(f"    masked_account_number example: {sample[0]}")
    print(f"    masked_utr_token example:      {sample[1]}")

    con.close()
    print(f"\nDatabase initialized at: {db_path.resolve()}")
    print("Ready for Text-to-SQL queries!\n")


if __name__ == "__main__":
    init_database()
