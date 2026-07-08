-- S1-011 citation support check records.
-- Extends the S1-005 v1 contract with one append-only table that persists the
-- citation support verdict and quote-fallback details beside each ledger claim.
-- Loaded after docs/s1-005-schema-v1.sql; never modifies S1-005 tables.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS claim_support_checks (
    check_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claim_ledger(claim_id),
    run_id TEXT NOT NULL REFERENCES extraction_runs(run_id),
    support_verdict TEXT NOT NULL CHECK (
        support_verdict IN ('yes', 'partial', 'no', 'unknown')
    ),
    check_method TEXT NOT NULL,
    quote_found INTEGER NOT NULL CHECK (quote_found IN (0, 1)),
    fallback_applied INTEGER NOT NULL CHECK (fallback_applied IN (0, 1)),
    original_claim_text TEXT,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_claim_support_checks_claim
    ON claim_support_checks(claim_id, created_at);

CREATE INDEX IF NOT EXISTS idx_claim_support_checks_verdict
    ON claim_support_checks(support_verdict, created_at);

CREATE TRIGGER IF NOT EXISTS claim_support_checks_no_update
BEFORE UPDATE ON claim_support_checks
BEGIN
    SELECT RAISE(ABORT, 'claim_support_checks is append-only');
END;

CREATE TRIGGER IF NOT EXISTS claim_support_checks_no_delete
BEFORE DELETE ON claim_support_checks
BEGIN
    SELECT RAISE(ABORT, 'claim_support_checks is append-only');
END;
