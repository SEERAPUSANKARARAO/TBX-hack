"""
Sample Data Generator — bank / account / transaction
======================================================
Generates a demo-sized dataset matching the TBX client schema exactly
(see db/schema.sql). Reuses the client's 10 real bank codes and the
narration STYLES observed in their sample export (NEFT/FT dash format,
UPI format, IMPS slash format, RTGS "R/" format) so core/description_parser.py
exercises the same patterns it was built against — just at a scale big
enough to demo aggregation, follow-ups, and anomaly detection.

Usage:
    python scripts/generate_sample_data.py
Writes: sample_data/bank.csv, sample_data/account.csv, sample_data/transaction.csv
"""
import csv
import random
import uuid
import base64
import os
from datetime import date, timedelta, datetime

random.seed(42)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT_ROOT, "sample_data")

TODAY = date(2026, 9, 5)

BANKS = [
    ("HDFC", "HDFC BANK LIMITED"),
    ("ICIC", "ICICI BANK LIMITED"),
    ("SBIN", "STATE BANK OF INDIA"),
    ("UTIB", "AXIS BANK LIMITED"),
    ("KKBK", "KOTAK MAHINDRA BANK LIMITED"),
    ("CNRB", "CANARA BANK"),
    ("UBIN", "UNION BANK OF INDIA"),
    ("AUBL", "AU SMALL FINANCE BANK LIMITED"),
    ("TMBL", "TAMILNAD MERCANTILE BANK LIMITED"),
    ("RATN", "RBL BANK LIMITED"),
]
BANK_CODES = [b[0] for b in BANKS]

# (display name, narration style, transaction_type, (low, high) typical amount)
COUNTERPARTIES = [
    ("SELECTION ELECTRONICS", "ft_dash", "debit", (5000, 90000)),
    ("SELECTION MOBILE", "neft_dash", "debit", (8000, 120000)),
    ("NAVYUG SELECTION", "upi", "debit", (2000, 60000)),
    ("UMANG SELECTION", "neft_dash", "debit", (3000, 40000)),
    ("SELECTRICITY TWO PRIVATE LIMITED", "rtgs", "credit", (50000, 400000)),
    ("RELIANCE DIGITAL RETAIL LTD", "ft_bare", "debit", (5000, 150000)),
    ("SELECTIONMALIGAI", "imps_slash", "credit", (5000, 70000)),
    ("PARESH VIKRANT GHASE", "neft_slash", "debit", (500, 15000)),
    ("GAUTAM SINGH", "imps_ow", "debit", (100, 5000)),
    ("BAJAJ FINANCE LIMITED", "neft_dash", "credit", (100000, 600000)),
    ("ZOMATO ONLINE ORDERING", "upi", "debit", (200, 2500)),
    ("SWIGGY INDIA", "upi", "debit", (150, 2200)),
    ("AMAZON RETAIL INDIA", "neft_dash", "debit", (500, 45000)),
    ("FLIPKART INTERNET PRIVATE LIMITED", "ft_bare", "debit", (500, 38000)),
    ("TATA POWER COMPANY LIMITED", "neft_dash", "debit", (2000, 18000)),
    ("BHARTI AIRTEL LIMITED", "neft_dash", "debit", (500, 9000)),
    ("IRCTC", "upi", "debit", (300, 6000)),
    ("LIC OF INDIA", "neft_slash", "debit", (5000, 35000)),
    ("GST PAYMENT NETWORK", "rtgs", "debit", (20000, 500000)),
    ("ICICI PRUDENTIAL MF", "neft_dash", "debit", (5000, 100000)),
]

# Counterparties given an occasional statistical outlier for anomaly detection.
ANOMALY_PRONE = {"SELECTION ELECTRONICS", "AMAZON RETAIL INDIA", "RELIANCE DIGITAL RETAIL LTD", "TATA POWER COMPANY LIMITED"}


def rand_digits(n):
    return "".join(random.choice("0123456789") for _ in range(n))


def rand_masked_utr():
    raw = os.urandom(random.randint(24, 40))
    return base64.b64encode(raw).decode("ascii")


def gen_description(name, style):
    if style == "ft_dash":
        return f"FT -  {rand_digits(8)} -  {rand_digits(14)} - {name}"
    if style == "neft_dash":
        ifsc = f"{random.choice(BANK_CODES)}{rand_digits(7)}"
        return f"NEFT  - {ifsc} - {rand_digits(8)} - {rand_digits(12)} - {name}"
    if style == "upi":
        masked_acct = f"XXXXXX{rand_digits(4)}"
        ifsc = f"{random.choice(BANK_CODES)}{rand_digits(7)}"
        return f"UPI-{name}-{masked_acct}-{ifsc}-{rand_digits(12)}-{rand_digits(18)}"
    if style == "imps_slash":
        return (f"IMPS/P2A/{rand_digits(12)}/{random.choice(BANK_CODES)}/{rand_digits(15)}/00/INET/"
                f"{rand_digits(4)}/{name.replace(' ', '')}/ZBFLCTP{rand_digits(1)}L2PBL{rand_digits(8)}/INWD{rand_digits(2)}")
    if style == "imps_ow":
        return f"IMPS OW/{rand_digits(12)}/{name.title()}/{random.choice(BANK_CODES)}/{rand_digits(11)}"
    if style == "neft_slash":
        return f"NEFT/{rand_digits(12)}/{random.choice(BANK_CODES)}/{name}"
    if style == "rtgs":
        ref = f"RATNR5{rand_digits(13)}"
        code = f"ZBFLCTP{rand_digits(3)}PBL{rand_digits(8)}"
        return f"R/{ref}/{code}//{name}/{ref} /{name}"
    if style == "ft_bare":
        # Keep the name naturally spaced — a real sample happens to show one
        # word glued to the field boundary, but collapsing the whole name
        # (as an earlier version of this generator did) produces a string no
        # user would ever type back in a follow-up question. No appended
        # location suffix either: it would fragment GROUP BY counterparty_name
        # into several near-duplicate rows for the same real counterparty.
        return f"FT-RE{rand_digits(10)}-{name}"
    return name


def random_date_weighted():
    """Skew toward the most recent 2 months so relative-date demo queries have data."""
    days_back_pool = list(range(0, 330))
    weights = [3.0 if d < 60 else 1.0 for d in days_back_pool]
    d = random.choices(days_back_pool, weights=weights, k=1)[0]
    dt = datetime.combine(TODAY - timedelta(days=d), datetime.min.time())
    dt = dt.replace(
        hour=random.randint(6, 22), minute=random.randint(0, 59),
        second=random.randint(0, 59), microsecond=random.randint(0, 999999),
    )
    return dt


def write_bank_csv():
    path = os.path.join(OUT_DIR, "bank.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bank_code", "bank_name"])
        for code, name in BANKS:
            w.writerow([code, name])
    print(f"  bank.csv: {len(BANKS)} rows")


def write_account_csv(n_accounts=32):
    path = os.path.join(OUT_DIR, "account.csv")
    accounts = []
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["account_id", "entity_id", "account_number", "program_id", "available_balance", "bank_code"])
        for i in range(n_accounts):
            account_id = str(uuid.uuid4())
            entity_id = str(uuid.uuid4())
            account_number = rand_digits(random.choice([14, 16]))
            program_id = random.choice([21, 4, 46])
            balance = round(random.uniform(5_000, 5_000_000), 2)
            if random.random() < 0.15:
                balance = -round(random.uniform(5_000, 2_000_000), 2)
            bank_code = random.choice(BANK_CODES)
            w.writerow([account_id, entity_id, account_number, program_id, balance, bank_code])
            accounts.append(account_id)
    print(f"  account.csv: {len(accounts)} rows")
    return accounts


def write_transaction_csv(accounts, n_transactions=450):
    path = os.path.join(OUT_DIR, "transaction.csv")
    rows_written = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "transaction_id", "account_id", "transaction_date", "transaction_type",
            "description", "transaction_amount", "transaction_reference_id", "utr_number",
        ])
        for _ in range(n_transactions):
            name, style, txn_type, (lo, hi) = random.choice(COUNTERPARTIES)
            account_id = random.choice(accounts)
            description = gen_description(name, style)

            amount = round(random.uniform(lo, hi), 2)
            if name in ANOMALY_PRONE and random.random() < 0.06:
                amount = round(amount * random.uniform(4, 8), 2)

            ref_id = rand_digits(random.choice([8, 9, 10])) if random.random() > 0.25 else None
            utr = rand_masked_utr() if random.random() > 0.30 else None

            w.writerow([
                str(uuid.uuid4()),
                account_id,
                random_date_weighted().strftime("%Y-%m-%d %H:%M:%S.%f"),
                txn_type,
                description,
                amount,
                ref_id if ref_id else "",
                utr if utr else "",
            ])
            rows_written += 1
    print(f"  transaction.csv: {rows_written} rows")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("Generating sample data...")
    write_bank_csv()
    accounts = write_account_csv()
    write_transaction_csv(accounts)

    # Remove the old (wrong-schema) sample CSVs so init_db.py can't
    # accidentally pick them up.
    for stale in ["chart_of_accounts.csv", "vendor_list.csv", "transactions.csv",
                  "vendor_payouts.csv", "reconciliation_status.csv"]:
        stale_path = os.path.join(OUT_DIR, stale)
        if os.path.exists(stale_path):
            os.remove(stale_path)
            print(f"  removed stale {stale}")

    print("Done.")


if __name__ == "__main__":
    main()
