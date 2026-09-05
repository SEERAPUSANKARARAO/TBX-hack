"""
Database Initialization Script (MySQL)
========================================
Creates schema (tables + views) in MySQL, ingests bank/account/transaction
CSVs, runs the description parser to populate transaction_derived, and
prints summary statistics. Connects as the full-privilege admin user
(DB_ADMIN_USER) — the runtime query path never uses this user.

Usage:
    python db/init_db.py
"""
import csv
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql
import pymysql.cursors
from pymysql.constants import CLIENT

from config import (
    DB_HOST, DB_PORT, DB_NAME, DB_ADMIN_USER, DB_ADMIN_PASSWORD,
    SAMPLE_DATA_DIR, CSV_TO_TABLE_MAP,
)
from core.description_parser import parse_description

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# FK-safe order for both drop (reverse) and load (forward).
TABLE_ORDER = ["bank", "account", "transaction"]


def _admin_connection():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_ADMIN_USER, password=DB_ADMIN_PASSWORD,
        database=DB_NAME, charset="utf8mb4", cursorclass=pymysql.cursors.Cursor,
        autocommit=True, client_flag=CLIENT.MULTI_STATEMENTS,
    )


def init_database():
    """Create the MySQL schema, ingest CSV data, derive counterparties."""
    con = _admin_connection()
    print(f"Connected to MySQL: {DB_HOST}:{DB_PORT}/{DB_NAME} as {DB_ADMIN_USER}")

    with con.cursor() as cur:
        # ── Step 0: Clean slate ──
        print("\nDropping existing tables (if any)...")
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for view in ["v_entity_lookup", "v_reconciliation_summary", "v_counterparty_lookup",
                     "v_counterparty_spend_summary", "v_transaction_enriched", "v_account_enriched"]:
            cur.execute(f"DROP VIEW IF EXISTS {view}")
        for table in ["transaction_derived"] + list(reversed(TABLE_ORDER)):
            cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")

        # ── Step 1: Schema DDL ──
        print("Creating schema (tables + views)...")
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        cur.execute(schema_sql)
        while cur.nextset():
            pass
        print("   Schema created successfully")

        # ── Step 2: Ingest CSVs (bank, account, transaction) ──
        print("\nIngesting CSV data...")
        for csv_file, table_name in CSV_TO_TABLE_MAP.items():
            csv_path = SAMPLE_DATA_DIR / csv_file
            if not csv_path.exists():
                print(f"   Skipping {csv_file} (file not found)")
                continue

            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                rows = [
                    [None if v == "" else v for v in row]
                    for row in reader
                ]

            if rows:
                placeholders = ", ".join(["%s"] * len(header))
                columns = ", ".join(header)
                cur.executemany(
                    f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})",
                    rows,
                )
            print(f"   {table_name}: {len(rows)} rows loaded from {csv_file}")

        # ── Step 3: Derive counterparty/rail info for every transaction ──
        print("\nRunning description parser to populate transaction_derived...")
        cur.execute("SELECT transaction_id, description FROM transaction")
        txns = cur.fetchall()
        confidence_counts = {"high": 0, "medium": 0, "low": 0}
        derived_rows = []
        for txn_id, description in txns:
            parsed = parse_description(description)
            confidence_counts[parsed["confidence"]] += 1
            derived_rows.append((txn_id, parsed["rail_type"], parsed["counterparty_name"], parsed["confidence"]))

        cur.executemany(
            "INSERT INTO transaction_derived (transaction_id, rail_type, counterparty_name, counterparty_confidence) "
            "VALUES (%s, %s, %s, %s)",
            derived_rows,
        )
        total = len(derived_rows)
        print(f"   Parsed {total} descriptions:")
        for level in ("high", "medium", "low"):
            pct = (confidence_counts[level] / total * 100) if total else 0
            print(f"     {level:6}: {confidence_counts[level]:4} ({pct:.1f}%)")

        # ── Step 4: Summary statistics ──
        print("\n" + "=" * 60)
        print("DATABASE SUMMARY")
        print("=" * 60)

        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = cur.fetchall()

        print(f"\n{'Table':<30} {'Rows':>10}")
        print("-" * 42)
        total_rows = 0
        for (table_name,) in tables:
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cur.fetchone()[0]
            total_rows += count
            print(f"  {table_name:<28} {count:>10,}")
        print("-" * 42)
        print(f"  {'TOTAL':<28} {total_rows:>10,}")

        cur.execute("""
            SELECT table_name FROM information_schema.views
            WHERE table_schema = DATABASE() ORDER BY table_name
        """)
        views = cur.fetchall()
        print(f"\nViews created: {len(views)}")
        for (view_name,) in views:
            print(f"   - {view_name}")

        # ── Step 5: Validation queries ──
        print("\n" + "=" * 60)
        print("VALIDATION QUERIES")
        print("=" * 60)

        cur.execute("SELECT MIN(transaction_date), MAX(transaction_date) FROM transaction")
        min_d, max_d = cur.fetchone()
        print(f"\n  Transaction date range: {min_d} to {max_d}")

        cur.execute("SELECT SUM(transaction_amount) FROM transaction WHERE transaction_type='debit'")
        total_debit = cur.fetchone()[0]
        cur.execute("SELECT SUM(transaction_amount) FROM transaction WHERE transaction_type='credit'")
        total_credit = cur.fetchone()[0]
        print(f"  Total debits:  {total_debit:,.2f}")
        print(f"  Total credits: {total_credit:,.2f}")

        print("\n  Entities with multiple accounts:")
        cur.execute("""
            SELECT entity_id, account_count, banks FROM v_entity_lookup
            WHERE account_count > 1 ORDER BY account_count DESC
        """)
        multi = cur.fetchall()
        if multi:
            for entity_id, cnt, banks in multi:
                print(f"    - {entity_id[:8]}...: {cnt} accounts across banks [{banks}]")
        else:
            print("    None — every entity_id owns exactly one account")

        print("\n  Top 5 counterparties by spend (debits, high/medium confidence only):")
        cur.execute("""
            SELECT counterparty_name, SUM(transaction_amount) AS total
            FROM v_transaction_enriched
            WHERE transaction_type = 'debit' AND counterparty_name IS NOT NULL
            GROUP BY counterparty_name ORDER BY total DESC LIMIT 5
        """)
        for name, total in cur.fetchall():
            print(f"    - {name}: {total:,.2f}")

        print("\n  Reconciliation-proxy breakdown (has_reference OR has_utr):")
        cur.execute("SELECT * FROM v_reconciliation_summary ORDER BY record_count DESC")
        for status, cnt, amt in cur.fetchall():
            print(f"    - {status}: {cnt} rows, {amt:,.2f} total")

        print("\n  Potential anomalies (>2.5 sigma above counterparty mean, debits):")
        cur.execute("""
            SELECT t.transaction_id, t.counterparty_name, t.transaction_amount, s.avg_amt, s.std_amt
            FROM v_transaction_enriched t
            JOIN (
                SELECT counterparty_name, AVG(transaction_amount) AS avg_amt, STDDEV_SAMP(transaction_amount) AS std_amt
                FROM v_transaction_enriched
                WHERE transaction_type = 'debit' AND counterparty_name IS NOT NULL
                GROUP BY counterparty_name HAVING COUNT(*) > 1
            ) s ON t.counterparty_name = s.counterparty_name
            WHERE s.std_amt > 0 AND t.transaction_amount > s.avg_amt + 2.5 * s.std_amt
        """)
        anomalies = cur.fetchall()
        if anomalies:
            for txn_id, name, amount, avg, std in anomalies:
                amount, avg = float(amount), float(avg)
                pct = ((amount - avg) / avg) * 100
                print(f"    - {txn_id[:8]}...: {amount:,.2f} to {name} ({pct:.0f}% above avg {avg:,.2f})")
        else:
            print("    None detected")

        print("\n  Sanity check — masked columns never expose raw PII:")
        cur.execute("SELECT masked_account_number, masked_utr_token FROM v_transaction_enriched WHERE masked_utr_token IS NOT NULL LIMIT 1")
        sample = cur.fetchone()
        print(f"    masked_account_number example: {sample[0]}")
        print(f"    masked_utr_token example:      {sample[1]}")

    con.close()
    print(f"\nDatabase initialized: {DB_NAME} @ {DB_HOST}:{DB_PORT}")
    print("Ready for Text-to-SQL queries!\n")


if __name__ == "__main__":
    init_database()
