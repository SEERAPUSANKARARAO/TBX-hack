-- ============================================================
-- Financial AI Chatbot – DuckDB Schema Definition
-- ============================================================
-- This DDL creates all tables, indexes, and optimized views
-- for the deterministic Text-to-SQL financial chatbot.
-- ============================================================

-- ── Table: chart_of_accounts ──
-- Master list of GL accounts used to categorize transactions.
CREATE TABLE IF NOT EXISTS chart_of_accounts (
    account_id      INTEGER PRIMARY KEY,
    account_code    VARCHAR NOT NULL UNIQUE,
    account_name    VARCHAR NOT NULL,
    account_type    VARCHAR NOT NULL,          -- Asset, Liability, Equity, Revenue, Expense
    parent_account_code VARCHAR,
    description     VARCHAR,
    is_active       BOOLEAN DEFAULT true
);

-- ── Table: vendor_list ──
-- Master vendor directory with aliases for fuzzy matching.
CREATE TABLE IF NOT EXISTS vendor_list (
    vendor_id       INTEGER PRIMARY KEY,
    vendor_name     VARCHAR NOT NULL,
    vendor_alias    VARCHAR,                   -- Short name / common alias
    category        VARCHAR,
    contact_email   VARCHAR,
    phone           VARCHAR,
    address         VARCHAR,
    payment_terms   VARCHAR,                   -- Net 15, Net 30, Net 45, Net 60
    is_active       BOOLEAN DEFAULT true
);

-- ── Table: transactions ──
-- Core financial transaction ledger.
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id    INTEGER PRIMARY KEY,
    transaction_date  DATE NOT NULL,
    posted_date       DATE,
    vendor_id         INTEGER REFERENCES vendor_list(vendor_id),
    vendor_name       VARCHAR NOT NULL,
    amount            DECIMAL(15, 2) NOT NULL,
    currency          VARCHAR DEFAULT 'USD',
    transaction_type  VARCHAR NOT NULL,        -- debit, credit
    account_code      VARCHAR REFERENCES chart_of_accounts(account_code),
    category          VARCHAR,
    description       VARCHAR,
    reference_number  VARCHAR,
    payment_method    VARCHAR                  -- ACH, Wire, Credit Card, Check
);

-- ── Table: vendor_payouts ──
-- Records of payments issued to vendors.
CREATE TABLE IF NOT EXISTS vendor_payouts (
    payout_id         INTEGER PRIMARY KEY,
    vendor_id         INTEGER REFERENCES vendor_list(vendor_id),
    vendor_name       VARCHAR NOT NULL,
    payout_date       DATE NOT NULL,
    amount            DECIMAL(15, 2) NOT NULL,
    currency          VARCHAR DEFAULT 'USD',
    payment_method    VARCHAR,
    bank_reference    VARCHAR,
    invoice_number    VARCHAR,
    status            VARCHAR NOT NULL,        -- completed, pending, failed
    notes             VARCHAR
);

-- ── Table: reconciliation_status ──
-- Links transactions to payouts with reconciliation state.
CREATE TABLE IF NOT EXISTS reconciliation_status (
    reconciliation_id   INTEGER PRIMARY KEY,
    transaction_id      INTEGER REFERENCES transactions(transaction_id),
    payout_id           INTEGER REFERENCES vendor_payouts(payout_id),
    reconciliation_date DATE,
    status              VARCHAR NOT NULL,      -- reconciled, unreconciled, pending
    matched_amount      DECIMAL(15, 2),
    variance            DECIMAL(15, 2) DEFAULT 0.00,
    variance_reason     VARCHAR,
    reconciled_by       VARCHAR,               -- system, manual_review
    notes               VARCHAR
);


-- ============================================================
-- OPTIMIZED VIEWS
-- ============================================================

-- ── View: v_transactions_enriched ──
-- Transactions joined with account and vendor details.
CREATE OR REPLACE VIEW v_transactions_enriched AS
SELECT
    t.transaction_id,
    t.transaction_date,
    t.posted_date,
    t.vendor_id,
    t.vendor_name,
    v.vendor_alias,
    v.category AS vendor_category,
    t.amount,
    t.currency,
    t.transaction_type,
    t.account_code,
    a.account_name,
    a.account_type,
    t.category AS expense_category,
    t.description,
    t.reference_number,
    t.payment_method,
    -- Date parts for easy filtering
    YEAR(t.transaction_date)    AS txn_year,
    MONTH(t.transaction_date)   AS txn_month,
    QUARTER(t.transaction_date) AS txn_quarter,
    DAYNAME(t.transaction_date) AS txn_day_of_week
FROM transactions t
LEFT JOIN vendor_list v ON t.vendor_id = v.vendor_id
LEFT JOIN chart_of_accounts a ON t.account_code = a.account_code;

-- ── View: v_reconciliation_details ──
-- Full reconciliation view joining transactions and payouts.
CREATE OR REPLACE VIEW v_reconciliation_details AS
SELECT
    r.reconciliation_id,
    r.status AS reconciliation_status,
    r.reconciliation_date,
    r.matched_amount,
    r.variance,
    r.variance_reason,
    r.reconciled_by,
    t.transaction_id,
    t.transaction_date,
    t.vendor_name,
    t.amount AS transaction_amount,
    t.category AS expense_category,
    t.reference_number AS transaction_ref,
    p.payout_id,
    p.payout_date,
    p.amount AS payout_amount,
    p.status AS payout_status,
    p.bank_reference,
    p.invoice_number
FROM reconciliation_status r
LEFT JOIN transactions t ON r.transaction_id = t.transaction_id
LEFT JOIN vendor_payouts p ON r.payout_id = p.payout_id;

-- ── View: v_vendor_spend_summary ──
-- Monthly spend summary per vendor.
CREATE OR REPLACE VIEW v_vendor_spend_summary AS
SELECT
    t.vendor_id,
    t.vendor_name,
    YEAR(t.transaction_date)  AS spend_year,
    MONTH(t.transaction_date) AS spend_month,
    COUNT(*)                  AS transaction_count,
    SUM(t.amount)             AS total_spend,
    AVG(t.amount)             AS avg_transaction,
    MIN(t.amount)             AS min_transaction,
    MAX(t.amount)             AS max_transaction
FROM transactions t
GROUP BY t.vendor_id, t.vendor_name,
         YEAR(t.transaction_date), MONTH(t.transaction_date);

-- ── View: v_reconciliation_summary ──
-- Reconciliation status aggregates.
CREATE OR REPLACE VIEW v_reconciliation_summary AS
SELECT
    status,
    COUNT(*)           AS record_count,
    SUM(variance)      AS total_variance,
    AVG(variance)      AS avg_variance
FROM reconciliation_status
GROUP BY status;


-- ============================================================
-- LOOKUP VIEWS (for entity resolution)
-- ============================================================

-- ── Lookup: All unique vendor names and aliases ──
CREATE OR REPLACE VIEW v_vendor_lookup AS
SELECT DISTINCT
    vendor_id,
    vendor_name,
    vendor_alias,
    category
FROM vendor_list
WHERE is_active = true;

-- ── Lookup: All reconciliation statuses ──
CREATE OR REPLACE VIEW v_reconciliation_statuses AS
SELECT DISTINCT status FROM reconciliation_status;

-- ── Lookup: All expense/payout categories ──
CREATE OR REPLACE VIEW v_expense_categories AS
SELECT DISTINCT category FROM transactions WHERE category IS NOT NULL;

-- ── Lookup: All account types ──
CREATE OR REPLACE VIEW v_account_types AS
SELECT DISTINCT account_type, account_code, account_name
FROM chart_of_accounts
WHERE is_active = true
ORDER BY account_code;
