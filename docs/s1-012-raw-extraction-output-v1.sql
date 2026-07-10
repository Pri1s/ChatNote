-- S1-012 raw model-output artifacts.
-- Stores one immutable JSON document for each extraction run that produced
-- parseable JSON, including runs later rejected by contract validation.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS extraction_outputs (
    run_id TEXT PRIMARY KEY REFERENCES extraction_runs(run_id),
    file_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TRIGGER IF NOT EXISTS extraction_outputs_no_update
BEFORE UPDATE ON extraction_outputs
BEGIN
    SELECT RAISE(ABORT, 'extraction_outputs is append-only');
END;

CREATE TRIGGER IF NOT EXISTS extraction_outputs_no_delete
BEFORE DELETE ON extraction_outputs
BEGIN
    SELECT RAISE(ABORT, 'extraction_outputs is append-only');
END;
