-- ============================================================
-- Financial AI Chatbot – MySQL Schema Definition
-- ============================================================
-- Base tables (bank / account / transaction) are the TBX client's DDL
-- verbatim — same column names, types, ENGINE/CHARSET. Do not add or
-- rename columns here. Everything derived (counterparty extraction,
-- masking, reconciliation proxy) lives in `transaction_derived` and the
-- views below, so the client tables stay pristine and swappable with a
-- real export.
-- ============================================================

CREATE TABLE IF NOT EXISTS bank (
    bank_code    VARCHAR(10)  PRIMARY KEY,
    bank_name    VARCHAR(150) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS account (
    account_id         VARCHAR(36)  PRIMARY KEY,
    entity_id          VARCHAR(36)  NOT NULL,
    account_number     VARCHAR(20)  NOT NULL,   -- SENSITIVE: never SELECT raw, use v_account_enriched.masked_account_number
    program_id         INT          NOT NULL,
    available_balance  DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    bank_code          VARCHAR(10)  NOT NULL,
    INDEX idx_account_entity_id (entity_id),
    FOREIGN KEY (bank_code) REFERENCES bank(bank_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS transaction (
    transaction_id           VARCHAR(36)  PRIMARY KEY,
    account_id               VARCHAR(36)  NOT NULL,
    transaction_date         TIMESTAMP(6) NOT NULL,
    transaction_type         ENUM('credit','debit') NOT NULL,
    description              VARCHAR(500) DEFAULT NULL,
    transaction_amount       DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    transaction_reference_id VARCHAR(64)  DEFAULT NULL,
    utr_number               VARCHAR(256) DEFAULT NULL,
    INDEX idx_txn_account_id (account_id),
    INDEX idx_txn_date (transaction_date),
    FOREIGN KEY (account_id) REFERENCES account(account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- DERIVED TABLE — populated once by db/init_db.py via
-- core/description_parser.py (deterministic regex, not an LLM call).
-- Kept separate from `transaction` so the client's raw schema is
-- never touched.
-- ============================================================
CREATE TABLE IF NOT EXISTS transaction_derived (
    transaction_id          VARCHAR(36) PRIMARY KEY,
    rail_type               VARCHAR(20),          -- UPI, NEFT, IMPS, FT, RTGS, OTHER
    counterparty_name       VARCHAR(200),         -- best-effort extraction from description; NULL if not confidently found
    counterparty_confidence VARCHAR(10),          -- 'high', 'medium', 'low'
    FOREIGN KEY (transaction_id) REFERENCES transaction(transaction_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- VIEWS
-- ============================================================

-- Accounts joined with bank details. account_number is masked here —
-- this is the ONLY account-level view SQL generation should read from.
CREATE OR REPLACE VIEW v_account_enriched AS
SELECT
    a.account_id,
    a.entity_id,
    CONCAT('******', RIGHT(a.account_number, 4)) AS masked_account_number,
    a.program_id,
    a.available_balance,
    a.bank_code,
    b.bank_name
FROM account a
LEFT JOIN bank b ON a.bank_code = b.bank_code;

-- Transactions joined with account/bank/derived-entity context.
-- Raw account_number and utr_number are deliberately NOT included —
-- masked_account_number / masked_utr_token stand in for them.
CREATE OR REPLACE VIEW v_transaction_enriched AS
SELECT
    t.transaction_id,
    t.account_id,
    t.transaction_date,
    t.transaction_type,
    t.description,
    t.transaction_amount,
    t.transaction_reference_id,
    CASE WHEN t.utr_number IS NOT NULL AND t.utr_number != ''
         THEN CONCAT('UTR-', SUBSTRING(MD5(t.utr_number), 1, 6))
         ELSE NULL END AS masked_utr_token,
    (t.transaction_reference_id IS NOT NULL AND t.transaction_reference_id != '') AS has_reference,
    (t.utr_number IS NOT NULL AND t.utr_number != '') AS has_utr,
    CASE WHEN (t.transaction_reference_id IS NOT NULL AND t.transaction_reference_id != '')
           OR (t.utr_number IS NOT NULL AND t.utr_number != '')
         THEN 'reconciled' ELSE 'unreconciled' END AS reconciliation_proxy_status,
    d.rail_type,
    d.counterparty_name,
    d.counterparty_confidence,
    a.entity_id,
    CONCAT('******', RIGHT(a.account_number, 4)) AS masked_account_number,
    a.program_id,
    a.bank_code,
    b.bank_name,
    YEAR(t.transaction_date)     AS txn_year,
    MONTH(t.transaction_date)    AS txn_month,
    QUARTER(t.transaction_date) AS txn_quarter,
    DAYNAME(t.transaction_date) AS txn_day_of_week,
    YEARWEEK(t.transaction_date, 3) AS txn_week  -- ISO-ish YYYYWW; group by this for week-wise breakdowns
FROM transaction t
LEFT JOIN transaction_derived d ON t.transaction_id = d.transaction_id
LEFT JOIN account a ON t.account_id = a.account_id
LEFT JOIN bank b ON a.bank_code = b.bank_code;

-- Per-counterparty monthly spend summary (debits only — spend direction).
-- SQL generation should prefer this for "how much did we spend on X"
-- style questions instead of re-aggregating the enriched view, since a
-- pre-aggregated view is far less prone to fan-out/double-counting.
CREATE OR REPLACE VIEW v_counterparty_spend_summary AS
SELECT
    counterparty_name,
    rail_type,
    entity_id,
    txn_year,
    txn_month,
    txn_week,
    COUNT(*)               AS transaction_count,
    SUM(transaction_amount) AS total_spend,
    AVG(transaction_amount) AS avg_transaction,
    MIN(transaction_amount) AS min_transaction,
    MAX(transaction_amount) AS max_transaction
FROM v_transaction_enriched
WHERE transaction_type = 'debit'
  AND counterparty_name IS NOT NULL
  AND counterparty_confidence != 'low'
GROUP BY counterparty_name, rail_type, entity_id, txn_year, txn_month, txn_week;

-- Lookup of distinct extracted counterparty names — feeds the entity
-- resolver's fuzzy-match index (replaces a vendor_list table, which
-- doesn't exist in the real client schema). Deliberately entity-agnostic
-- (one row per name globally) — this is the resolver's global search index,
-- not a per-customer view. api/main.py's /api/counterparties?entity_id=
-- queries v_transaction_enriched directly instead when it needs a
-- per-customer counterparty list, so this view's grain stays untouched.
CREATE OR REPLACE VIEW v_counterparty_lookup AS
SELECT
    counterparty_name,
    rail_type,
    COUNT(*) AS mention_count
FROM transaction_derived
WHERE counterparty_name IS NOT NULL
  AND counterparty_confidence != 'low'
GROUP BY counterparty_name, rail_type;

-- Reconciliation-proxy breakdown (see column comment above — this is a
-- heuristic based on presence of a reference/UTR, not a definitive
-- accounting reconciliation status).
CREATE OR REPLACE VIEW v_reconciliation_summary AS
SELECT
    reconciliation_proxy_status,
    COUNT(*)                    AS record_count,
    SUM(transaction_amount)     AS total_amount
FROM v_transaction_enriched
GROUP BY reconciliation_proxy_status;

-- One row per customer entity, with account/bank counts — feeds the
-- entity-selector dropdown (there is no login/auth in this build, so this
-- is the stand-in for "which customer am I looking at").
CREATE OR REPLACE VIEW v_entity_lookup AS
SELECT
    a.entity_id,
    COUNT(DISTINCT a.account_id) AS account_count,
    COUNT(DISTINCT a.bank_code)  AS bank_count,
    GROUP_CONCAT(DISTINCT a.bank_code ORDER BY a.bank_code SEPARATOR ', ') AS banks,
    GROUP_CONCAT(DISTINCT b.bank_name ORDER BY b.bank_name SEPARATOR ', ') AS bank_names
FROM account a
LEFT JOIN bank b ON a.bank_code = b.bank_code
GROUP BY a.entity_id;
