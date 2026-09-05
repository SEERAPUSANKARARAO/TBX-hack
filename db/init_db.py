"""
Database Initialization Script
==============================
Ingests all CSV sample data into a DuckDB database, creates schema
(tables + views), and prints summary statistics.

Usage:
    python db/init_db.py
"""
import sys
import os
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add project root to path so we can import config
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb
from config import DUCKDB_PATH, SAMPLE_DATA_DIR, CSV_TO_TABLE_MAP

DB_DIR = Path(DUCKDB_PATH).parent
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_database():
    """Create the DuckDB database, load schema, and ingest CSV data."""

    # Ensure the db directory exists
    DB_DIR.mkdir(parents=True, exist_ok=True)

    # Remove existing database for a clean start
    db_path = Path(DUCKDB_PATH)
    if db_path.exists():
        db_path.unlink()
        print(f"🗑️  Removed existing database: {db_path}")

    # Connect to DuckDB (creates the file)
    con = duckdb.connect(str(db_path))
    print(f"✅ Connected to DuckDB: {db_path}")

    # ── Step 1: Execute schema DDL ──
    print("\n📋 Creating schema (tables + views)...")
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    con.execute(schema_sql)
    print("   ✅ Schema created successfully")

    # ── Step 2: Ingest CSV files ──
    print("\n📥 Ingesting CSV data...")
    for csv_file, table_name in CSV_TO_TABLE_MAP.items():
        csv_path = SAMPLE_DATA_DIR / csv_file
        if not csv_path.exists():
            print(f"   ⚠️  Skipping {csv_file} (file not found)")
            continue

        # Use DuckDB's native CSV reader for fast ingestion
        con.execute(f"""
            INSERT INTO {table_name}
            SELECT * FROM read_csv_auto('{csv_path.as_posix()}',
                                        header=true,
                                        ignore_errors=true)
        """)
        row_count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"   ✅ {table_name}: {row_count} rows loaded from {csv_file}")

    # ── Step 3: Print summary statistics ──
    print("\n" + "=" * 60)
    print("📊 DATABASE SUMMARY")
    print("=" * 60)

    # Table row counts
    tables = con.execute("""
        SELECT table_name
        FROM information_schema.tables
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

    # View counts
    views = con.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_type = 'VIEW'
        ORDER BY table_name
    """).fetchall()
    print(f"\n📐 Views created: {len(views)}")
    for (view_name,) in views:
        print(f"   • {view_name}")

    # ── Step 4: Run sample queries to validate ──
    print("\n" + "=" * 60)
    print("🔍 VALIDATION QUERIES")
    print("=" * 60)

    # Total spend
    result = con.execute("SELECT SUM(amount) FROM transactions").fetchone()[0]
    print(f"\n  Total transaction volume:  ${result:,.2f}")

    # Top 3 vendors by spend
    print("\n  Top 3 vendors by spend:")
    top_vendors = con.execute("""
        SELECT vendor_name, SUM(amount) as total
        FROM transactions
        GROUP BY vendor_name
        ORDER BY total DESC
        LIMIT 3
    """).fetchall()
    for vendor, total in top_vendors:
        print(f"    • {vendor}: ${total:,.2f}")

    # Reconciliation breakdown
    print("\n  Reconciliation status breakdown:")
    recon_stats = con.execute("""
        SELECT status, COUNT(*) as cnt,
               ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 1) as pct
        FROM reconciliation_status
        GROUP BY status
        ORDER BY cnt DESC
    """).fetchall()
    for status, cnt, pct in recon_stats:
        print(f"    • {status}: {cnt} ({pct}%)")

    # Anomaly check
    print("\n  🚨 Potential anomalies (>2.5σ above vendor mean):")
    anomalies = con.execute("""
        WITH vendor_stats AS (
            SELECT vendor_id, vendor_name,
                   AVG(amount) AS avg_amount,
                   STDDEV(amount) AS std_amount
            FROM transactions
            GROUP BY vendor_id, vendor_name
            HAVING COUNT(*) > 1
        )
        SELECT t.transaction_id, t.vendor_name, t.amount,
               vs.avg_amount, vs.std_amount,
               t.description
        FROM transactions t
        JOIN vendor_stats vs ON t.vendor_id = vs.vendor_id
        WHERE vs.std_amount > 0
          AND t.amount > vs.avg_amount + 2.5 * vs.std_amount
    """).fetchall()

    if anomalies:
        for txn_id, vendor, amount, avg, std, desc in anomalies:
            pct_above = ((amount - avg) / avg) * 100
            print(f"    ⚠️  Txn #{txn_id}: ${amount:,.2f} to {vendor}")
            print(f"       ({pct_above:.0f}% above avg ${avg:,.2f}) — {desc}")
    else:
        print("    None detected")

    con.close()
    print(f"\n✅ Database initialized at: {db_path.resolve()}")
    print("   Ready for Text-to-SQL queries!\n")


if __name__ == "__main__":
    init_database()
