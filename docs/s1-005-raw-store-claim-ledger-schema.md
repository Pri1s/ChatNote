# S1-005 Raw Store and Claim Ledger Schema

## Purpose

This is the Sprint 1 schema contract for ChatNote's knowledge-base foundation. It defines a local-first SQLite v1 store for:

- immutable Claude shared-snapshot source artifacts;
- immutable parsed transcript records produced by the current capture pipeline;
- append-only extraction run and claim ledger records.

The executable DDL is in [s1-005-schema-v1.sql](./s1-005-schema-v1.sql). That SQL file is the authoritative machine-checkable contract for S1-006 implementation.

S1-005 is intentionally a design deliverable. It does not wire the capture CLI into storage, create query commands, or implement extraction.

## Current Transcript Input

The current capture path writes private local files under ignored `data/raw/` and `data/transcripts/` paths. Parsed transcript JSON is shaped by `src/chatnote/models.py` and currently includes:

- `source`: `url`, `capture_method`, `parser_method`, `fetched_at`, and `raw_sha256`.
- `conversation`: `id` and nullable `title`.
- `messages`: ordered records with `index`, `role`, `text`, `blocks`, nullable `timestamp`, and `provenance`.
- `messages[].blocks`: ordered blocks with `type`, `text`, optional `language`, and optional `metadata`.
- `warnings`: parser warnings with `code`, `message`, and optional `message_index`.

Supported message roles are `user`, `assistant`, `system`, and `unknown`. Supported block types are `text`, `code`, `table`, `attachment`, and `tool`.

## Storage Rules

Raw artifacts and parsed transcripts are immutable after insert. If the same Claude share is captured more than once, each raw source and transcript JSON file receives its own artifact row keyed by checksum/path, not an overwrite.

The claim ledger is append-only. Corrections or re-extractions create new rows. `supersedes_claim_id` may point to an older claim, but old claim rows are not updated or deleted.

Extraction runs are also append-only audit records in v1. S1-010 should insert the final run outcome once an extraction attempt has completed or failed.

Concept linking is out of scope for Sprint 1. `concept_tags_json` is a JSON array of free-form strings and must not require a concept dictionary, graph, or canonical concept table.

## SQLite V1 Contract

### Raw and Transcript Tables

`raw_artifacts`

| Column | Required | Notes |
| --- | --- | --- |
| `artifact_id` | Yes | Application-generated stable ID. |
| `conversation_id` | Yes | Claude shared-snapshot ID. |
| `source_url` | Yes | Original Claude share URL. |
| `artifact_kind` | Yes | `source_snapshot`, `parsed_transcript`, or `auxiliary`. |
| `file_path` | Yes | Local path to the immutable artifact; unique. |
| `media_type` | Yes | Example: `application/json` or `text/html`. |
| `sha256` | Yes | Hex SHA-256 of the artifact bytes/text. |
| `byte_size` | Yes | Non-negative size in bytes. |
| `captured_at` | Yes | Capture timestamp from the current pipeline. |
| `created_at` | Yes | Store insertion timestamp. |

`transcripts`

| Column | Required | Notes |
| --- | --- | --- |
| `transcript_id` | Yes | Application-generated stable ID. |
| `conversation_id` | Yes | Duplicated for direct conversation lookup. |
| `title` | No | Conversation title when available. |
| `source_artifact_id` | Yes | Raw Claude source artifact. |
| `transcript_artifact_id` | Yes | Parsed transcript JSON artifact. |
| `parser_method` | Yes | Current examples: `json_structured_data`, `embedded_structured_data`, `rendered_html`. |
| `fetched_at` | Yes | Source fetch timestamp from transcript JSON. |
| `message_count` | Yes | Number of transcript messages. |
| `warning_count` | Yes | Number of parser warnings. |
| `created_at` | Yes | Store insertion timestamp. |

`transcript_messages`, `transcript_message_blocks`, and `transcript_warnings` normalize the parsed JSON while preserving message order, block order, nullable timestamps, provenance JSON, metadata JSON, and parser warning linkage.

### Extraction and Ledger Tables

`extraction_runs` records one completed or failed extraction attempt for one transcript. It stores extractor identity, prompt version/hash, model, final status, error text, input size, output claim count, and run timestamps.

`claim_ledger` contains one append-only row per extracted claim:

| Column | Required | Notes |
| --- | --- | --- |
| `claim_id` | Yes | Application-generated stable ID. |
| `run_id` | Yes | Extraction attempt that produced the claim. |
| `transcript_id` | Yes | Transcript containing the source message. |
| `conversation_id` | Yes | Duplicated for direct query by conversation. |
| `claim_sequence` | Yes | Claim order within the extraction run. |
| `standalone_claim_text` | Yes | The ledger-ready standalone statement. |
| `speaker_role` | Yes | `user`, `assistant`, `system`, or `unknown`. |
| `speaker_label` | No | Optional display label if later extraction can identify one. |
| `speech_act_type` | Yes | One of the Sprint 1 speech-act labels. |
| `hedge_level` | Yes | One of the Sprint 1 hedge labels. |
| `source_message_index` | Yes | Required source pointer to the transcript message. |
| `source_block_index` | No | Optional block-level pointer. |
| `source_char_start` | No | Optional character start offset within the source block/message text. |
| `source_char_end` | No | Optional character end offset; must be greater than start. |
| `source_quote` | Yes | Verbatim evidence span or shortest available quote. |
| `source_timestamp` | No | Copied from source message timestamp when available. |
| `concept_tags_json` | Yes | JSON array of free concept tags. |
| `supersedes_claim_id` | No | Optional pointer to an older claim. |
| `created_at` | Yes | Store insertion timestamp. |

### Enumerations

`speech_act_type` values:

- `fact`
- `preference`
- `decision`
- `instruction`
- `question`
- `plan`
- `todo`
- `correction`
- `summary`
- `other`

`hedge_level` values:

- `none`
- `low`
- `medium`
- `high`
- `unknown`

## Downstream Contracts

S1-006 should initialize SQLite from `docs/s1-005-schema-v1.sql`, insert current capture outputs into the raw/transcript tables, and document setup/inspection commands. It should not relax immutability or overwrite local artifact rows.

S1-007 should use the provided indexes for queries by conversation, `speaker_role`, and `speech_act_type`. Query results must preserve source pointers back to `transcript_id` plus `source_message_index`, with optional block/character precision when present.

S1-009 should make its prompt output map directly into `claim_ledger`: standalone claim text, speaker role, speech-act type, hedge level, source message pointer, source quote, timestamp when available, and free concept tags.

S1-010 should run one conversation at a time, create one `extraction_runs` row per completed or failed attempt, validate the prompt output against the ledger constraints, and insert claims without updating earlier rows.

## Verification

The SQL artifact must pass these checks before S1-006 consumes it:

- load successfully into an in-memory SQLite database with `PRAGMA foreign_keys = ON`;
- expose required tables, constraints, triggers, and indexes;
- reject `UPDATE` and `DELETE` for raw artifacts, transcripts, transcript message records, extraction runs, and claim ledger rows;
- support query paths by conversation, speaker role, and speech-act type.
