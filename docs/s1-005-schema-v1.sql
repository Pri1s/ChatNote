PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS raw_artifacts (
    artifact_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    artifact_kind TEXT NOT NULL CHECK (
        artifact_kind IN ('source_snapshot', 'parsed_transcript', 'auxiliary')
    ),
    file_path TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS transcripts (
    transcript_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    title TEXT,
    source_artifact_id TEXT NOT NULL REFERENCES raw_artifacts(artifact_id),
    transcript_artifact_id TEXT NOT NULL REFERENCES raw_artifacts(artifact_id),
    parser_method TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    message_count INTEGER NOT NULL CHECK (message_count >= 0),
    warning_count INTEGER NOT NULL CHECK (warning_count >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (conversation_id, transcript_artifact_id)
);

CREATE TABLE IF NOT EXISTS transcript_messages (
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    conversation_id TEXT NOT NULL,
    message_index INTEGER NOT NULL CHECK (message_index >= 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'unknown')),
    text TEXT NOT NULL,
    timestamp TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(provenance_json)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (transcript_id, message_index)
);

CREATE TABLE IF NOT EXISTS transcript_message_blocks (
    transcript_id TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    block_index INTEGER NOT NULL CHECK (block_index >= 0),
    block_type TEXT NOT NULL CHECK (
        block_type IN ('text', 'code', 'table', 'attachment', 'tool')
    ),
    text TEXT NOT NULL,
    language TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (transcript_id, message_index, block_index),
    FOREIGN KEY (transcript_id, message_index)
        REFERENCES transcript_messages(transcript_id, message_index)
);

CREATE TABLE IF NOT EXISTS transcript_warnings (
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    warning_index INTEGER NOT NULL CHECK (warning_index >= 0),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    message_index INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (transcript_id, warning_index),
    FOREIGN KEY (transcript_id, message_index)
        REFERENCES transcript_messages(transcript_id, message_index)
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    run_id TEXT PRIMARY KEY,
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    extractor_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_sha256 TEXT,
    model TEXT,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'partial')),
    error_message TEXT,
    input_message_count INTEGER NOT NULL CHECK (input_message_count >= 0),
    output_claim_count INTEGER NOT NULL CHECK (output_claim_count >= 0),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS claim_ledger (
    claim_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES extraction_runs(run_id),
    transcript_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    claim_sequence INTEGER NOT NULL CHECK (claim_sequence >= 0),
    standalone_claim_text TEXT NOT NULL CHECK (length(trim(standalone_claim_text)) > 0),
    speaker_role TEXT NOT NULL CHECK (
        speaker_role IN ('user', 'assistant', 'system', 'unknown')
    ),
    speaker_label TEXT,
    speech_act_type TEXT NOT NULL CHECK (
        speech_act_type IN (
            'fact',
            'preference',
            'decision',
            'instruction',
            'question',
            'plan',
            'todo',
            'correction',
            'summary',
            'other'
        )
    ),
    hedge_level TEXT NOT NULL DEFAULT 'unknown' CHECK (
        hedge_level IN ('none', 'low', 'medium', 'high', 'unknown')
    ),
    source_message_index INTEGER NOT NULL CHECK (source_message_index >= 0),
    source_block_index INTEGER CHECK (source_block_index IS NULL OR source_block_index >= 0),
    source_char_start INTEGER CHECK (source_char_start IS NULL OR source_char_start >= 0),
    source_char_end INTEGER CHECK (
        source_char_end IS NULL
        OR (source_char_start IS NOT NULL AND source_char_end > source_char_start)
    ),
    source_quote TEXT NOT NULL CHECK (length(trim(source_quote)) > 0),
    source_timestamp TEXT,
    concept_tags_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(concept_tags_json)
        AND json_type(concept_tags_json) = 'array'
    ),
    supersedes_claim_id TEXT REFERENCES claim_ledger(claim_id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (run_id, claim_sequence),
    FOREIGN KEY (transcript_id, source_message_index)
        REFERENCES transcript_messages(transcript_id, message_index)
);

CREATE INDEX IF NOT EXISTS idx_raw_artifacts_conversation
    ON raw_artifacts(conversation_id, captured_at);

CREATE INDEX IF NOT EXISTS idx_transcripts_conversation
    ON transcripts(conversation_id, fetched_at);

CREATE INDEX IF NOT EXISTS idx_transcript_messages_conversation
    ON transcript_messages(conversation_id, message_index);

CREATE INDEX IF NOT EXISTS idx_extraction_runs_transcript
    ON extraction_runs(transcript_id, started_at);

CREATE INDEX IF NOT EXISTS idx_claim_ledger_conversation
    ON claim_ledger(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_claim_ledger_speaker_role
    ON claim_ledger(speaker_role, conversation_id);

CREATE INDEX IF NOT EXISTS idx_claim_ledger_speech_act_type
    ON claim_ledger(speech_act_type, conversation_id);

CREATE INDEX IF NOT EXISTS idx_claim_ledger_source_pointer
    ON claim_ledger(transcript_id, source_message_index, source_block_index);

CREATE INDEX IF NOT EXISTS idx_claim_ledger_supersedes
    ON claim_ledger(supersedes_claim_id);

CREATE TRIGGER IF NOT EXISTS raw_artifacts_no_update
BEFORE UPDATE ON raw_artifacts
BEGIN
    SELECT RAISE(ABORT, 'raw_artifacts is immutable');
END;

CREATE TRIGGER IF NOT EXISTS raw_artifacts_no_delete
BEFORE DELETE ON raw_artifacts
BEGIN
    SELECT RAISE(ABORT, 'raw_artifacts is immutable');
END;

CREATE TRIGGER IF NOT EXISTS transcripts_no_update
BEFORE UPDATE ON transcripts
BEGIN
    SELECT RAISE(ABORT, 'transcripts is immutable');
END;

CREATE TRIGGER IF NOT EXISTS transcripts_no_delete
BEFORE DELETE ON transcripts
BEGIN
    SELECT RAISE(ABORT, 'transcripts is immutable');
END;

CREATE TRIGGER IF NOT EXISTS transcript_messages_no_update
BEFORE UPDATE ON transcript_messages
BEGIN
    SELECT RAISE(ABORT, 'transcript_messages is immutable');
END;

CREATE TRIGGER IF NOT EXISTS transcript_messages_no_delete
BEFORE DELETE ON transcript_messages
BEGIN
    SELECT RAISE(ABORT, 'transcript_messages is immutable');
END;

CREATE TRIGGER IF NOT EXISTS transcript_message_blocks_no_update
BEFORE UPDATE ON transcript_message_blocks
BEGIN
    SELECT RAISE(ABORT, 'transcript_message_blocks is immutable');
END;

CREATE TRIGGER IF NOT EXISTS transcript_message_blocks_no_delete
BEFORE DELETE ON transcript_message_blocks
BEGIN
    SELECT RAISE(ABORT, 'transcript_message_blocks is immutable');
END;

CREATE TRIGGER IF NOT EXISTS transcript_warnings_no_update
BEFORE UPDATE ON transcript_warnings
BEGIN
    SELECT RAISE(ABORT, 'transcript_warnings is immutable');
END;

CREATE TRIGGER IF NOT EXISTS transcript_warnings_no_delete
BEFORE DELETE ON transcript_warnings
BEGIN
    SELECT RAISE(ABORT, 'transcript_warnings is immutable');
END;

CREATE TRIGGER IF NOT EXISTS extraction_runs_no_update
BEFORE UPDATE ON extraction_runs
BEGIN
    SELECT RAISE(ABORT, 'extraction_runs is append-only');
END;

CREATE TRIGGER IF NOT EXISTS extraction_runs_no_delete
BEFORE DELETE ON extraction_runs
BEGIN
    SELECT RAISE(ABORT, 'extraction_runs is append-only');
END;

CREATE TRIGGER IF NOT EXISTS claim_ledger_no_update
BEFORE UPDATE ON claim_ledger
BEGIN
    SELECT RAISE(ABORT, 'claim_ledger is append-only');
END;

CREATE TRIGGER IF NOT EXISTS claim_ledger_no_delete
BEFORE DELETE ON claim_ledger
BEGIN
    SELECT RAISE(ABORT, 'claim_ledger is append-only');
END;
