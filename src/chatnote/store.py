from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA_FILE_NAMES = (
    "s1-005-schema-v1.sql",
    "s1-011-citation-support-v1.sql",
    "s1-012-raw-extraction-output-v1.sql",
)
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"
DEFAULT_DB_PATH = Path("data") / "chatnote.db"

ARTIFACT_KINDS = ("source_snapshot", "parsed_transcript", "auxiliary")
SPEAKER_ROLES = ("user", "assistant", "system", "unknown")
SPEECH_ACT_TYPES = (
    "fact",
    "preference",
    "decision",
    "instruction",
    "question",
    "plan",
    "todo",
    "correction",
    "summary",
    "other",
)
HEDGE_LEVELS = ("none", "low", "medium", "high", "unknown")
SUPPORT_VERDICTS = ("yes", "partial", "no", "unknown")
RUN_STATUSES = ("succeeded", "failed", "partial")

_MEDIA_TYPES = {
    ".json": "application/json",
    ".html": "text/html",
}


class StoreError(RuntimeError):
    """Raised when a storage operation cannot be completed."""


class DuplicateArtifactError(StoreError):
    """Raised when an artifact path is registered a second time."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    transcript_id: str
    conversation_id: str
    source_artifact_id: str
    transcript_artifact_id: str
    message_count: int
    warning_count: int


@dataclass(frozen=True, slots=True)
class RunRecord:
    transcript_id: str
    extractor_name: str
    prompt_version: str
    status: str
    input_message_count: int
    output_claim_count: int
    started_at: str
    prompt_sha256: str | None = None
    model: str | None = None
    error_message: str | None = None
    completed_at: str | None = None


class LedgerStore:
    """Local-first SQLite store for the S1-005 raw store and claim ledger contract.

    Immutability and append-only rules are enforced by the schema triggers;
    this class intentionally exposes no update or delete operations.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.row_factory = sqlite3.Row

    @classmethod
    def open(cls, db_path: str | Path = DEFAULT_DB_PATH) -> "LedgerStore":
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(db_path))
        connection.execute("PRAGMA foreign_keys = ON")
        for schema_file in SCHEMA_FILE_NAMES:
            schema_path = DOCS_DIR / schema_file
            if not schema_path.is_file():
                raise StoreError(f"Schema file not found: {schema_path}")
            connection.executescript(schema_path.read_text(encoding="utf-8"))
        return cls(connection)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LedgerStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def register_artifact(
        self,
        *,
        conversation_id: str,
        source_url: str,
        artifact_kind: str,
        file_path: str | Path,
        captured_at: str,
    ) -> str:
        if artifact_kind not in ARTIFACT_KINDS:
            raise StoreError(f"Unsupported artifact kind: {artifact_kind!r}")
        path = Path(file_path)
        if not path.is_file():
            raise StoreError(f"Artifact file not found: {path}")
        data = path.read_bytes()
        artifact_id = _new_id("art")
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO raw_artifacts (
                        artifact_id, conversation_id, source_url, artifact_kind,
                        file_path, media_type, sha256, byte_size, captured_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        conversation_id,
                        source_url,
                        artifact_kind,
                        str(path),
                        _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                        hashlib.sha256(data).hexdigest(),
                        len(data),
                        captured_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "raw_artifacts.file_path" in str(exc):
                raise DuplicateArtifactError(
                    f"Artifact already registered for path: {path}"
                ) from exc
            raise StoreError(f"Artifact insert rejected: {exc}") from exc
        return artifact_id

    def ingest_capture(
        self, *, raw_path: str | Path, transcript_path: str | Path
    ) -> IngestResult:
        """Register one capture output pair and store the parsed transcript.

        Reads the transcript JSON written by the capture pipeline, registers the
        raw source and transcript artifacts, and inserts immutable transcript,
        message, block, and warning rows.
        """
        transcript_file = Path(transcript_path)
        try:
            transcript = json.loads(transcript_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"Could not read transcript JSON: {exc}") from exc

        source = transcript.get("source", {})
        conversation = transcript.get("conversation", {})
        messages = transcript.get("messages", [])
        warnings = transcript.get("warnings", [])
        conversation_id = conversation.get("id")
        source_url = source.get("url")
        fetched_at = source.get("fetched_at")
        parser_method = source.get("parser_method")
        if not conversation_id or not source_url or not fetched_at or not parser_method:
            raise StoreError(
                "Transcript JSON is missing required source/conversation metadata."
            )

        source_artifact_id = self.register_artifact(
            conversation_id=conversation_id,
            source_url=source_url,
            artifact_kind="source_snapshot",
            file_path=raw_path,
            captured_at=fetched_at,
        )
        transcript_artifact_id = self.register_artifact(
            conversation_id=conversation_id,
            source_url=source_url,
            artifact_kind="parsed_transcript",
            file_path=transcript_file,
            captured_at=fetched_at,
        )

        transcript_id = _new_id("tr")
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO transcripts (
                        transcript_id, conversation_id, title, source_artifact_id,
                        transcript_artifact_id, parser_method, fetched_at,
                        message_count, warning_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        transcript_id,
                        conversation_id,
                        conversation.get("title"),
                        source_artifact_id,
                        transcript_artifact_id,
                        parser_method,
                        fetched_at,
                        len(messages),
                        len(warnings),
                    ),
                )
                for message in messages:
                    self._conn.execute(
                        """
                        INSERT INTO transcript_messages (
                            transcript_id, conversation_id, message_index, role,
                            text, timestamp, provenance_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            transcript_id,
                            conversation_id,
                            message["index"],
                            message["role"],
                            message["text"],
                            message.get("timestamp"),
                            json.dumps(message.get("provenance", {}), ensure_ascii=False),
                        ),
                    )
                    for block_index, block in enumerate(message.get("blocks", [])):
                        self._conn.execute(
                            """
                            INSERT INTO transcript_message_blocks (
                                transcript_id, message_index, block_index,
                                block_type, text, language, metadata_json
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                transcript_id,
                                message["index"],
                                block_index,
                                block["type"],
                                block["text"],
                                block.get("language"),
                                json.dumps(block.get("metadata", {}), ensure_ascii=False),
                            ),
                        )
                for warning_index, warning in enumerate(warnings):
                    self._conn.execute(
                        """
                        INSERT INTO transcript_warnings (
                            transcript_id, warning_index, code, message, message_index
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            transcript_id,
                            warning_index,
                            warning["code"],
                            warning["message"],
                            warning.get("message_index"),
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"Transcript insert rejected: {exc}") from exc

        return IngestResult(
            transcript_id=transcript_id,
            conversation_id=conversation_id,
            source_artifact_id=source_artifact_id,
            transcript_artifact_id=transcript_artifact_id,
            message_count=len(messages),
            warning_count=len(warnings),
        )

    def record_extraction_result(
        self,
        *,
        run: RunRecord,
        claims: list[dict[str, Any]] | None = None,
        support_checks: list[dict[str, Any]] | None = None,
        raw_output_path: str | Path | None = None,
    ) -> tuple[str, list[str]]:
        """Append one extraction run, raw JSON output, claims, and checks atomically.

        `claims` rows use claim_ledger column names (without claim_id/run_id,
        which are generated here). `support_checks` rows reference claims by
        `claim_sequence` and use claim_support_checks column names. When a
        parseable model response was written to `raw_output_path`, its immutable
        JSON document is linked to this run.
        """
        if run.status not in RUN_STATUSES:
            raise StoreError(f"Unsupported run status: {run.status!r}")
        claims = claims or []
        support_checks = support_checks or []
        output_details = _read_output_details(raw_output_path)
        run_id = _new_id("run")
        claim_ids: dict[int, str] = {
            claim["claim_sequence"]: _new_id("claim") for claim in claims
        }
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO extraction_runs (
                        run_id, transcript_id, extractor_name, prompt_version,
                        prompt_sha256, model, status, error_message,
                        input_message_count, output_claim_count, started_at, completed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        run.transcript_id,
                        run.extractor_name,
                        run.prompt_version,
                        run.prompt_sha256,
                        run.model,
                        run.status,
                        run.error_message,
                        run.input_message_count,
                        run.output_claim_count,
                        run.started_at,
                        run.completed_at,
                    ),
                )
                if output_details is not None:
                    file_path, sha256, byte_size = output_details
                    self._conn.execute(
                        """
                        INSERT INTO extraction_outputs (
                            run_id, file_path, sha256, byte_size
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (run_id, file_path, sha256, byte_size),
                    )
                for claim in claims:
                    self._conn.execute(
                        """
                        INSERT INTO claim_ledger (
                            claim_id, run_id, transcript_id, conversation_id,
                            claim_sequence, standalone_claim_text, speaker_role,
                            speaker_label, speech_act_type, hedge_level,
                            source_message_index, source_block_index,
                            source_char_start, source_char_end, source_quote,
                            source_timestamp, concept_tags_json, supersedes_claim_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            claim_ids[claim["claim_sequence"]],
                            run_id,
                            claim["transcript_id"],
                            claim["conversation_id"],
                            claim["claim_sequence"],
                            claim["standalone_claim_text"],
                            claim["speaker_role"],
                            claim.get("speaker_label"),
                            claim["speech_act_type"],
                            claim["hedge_level"],
                            claim["source_message_index"],
                            claim.get("source_block_index"),
                            claim.get("source_char_start"),
                            claim.get("source_char_end"),
                            claim["source_quote"],
                            claim.get("source_timestamp"),
                            claim.get("concept_tags_json", "[]"),
                            claim.get("supersedes_claim_id"),
                        ),
                    )
                for check in support_checks:
                    if check["claim_sequence"] not in claim_ids:
                        raise StoreError(
                            "Support check references unknown claim_sequence "
                            f"{check['claim_sequence']}."
                        )
                    self._conn.execute(
                        """
                        INSERT INTO claim_support_checks (
                            check_id, claim_id, run_id, support_verdict, check_method,
                            quote_found, fallback_applied, original_claim_text, detail
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            _new_id("check"),
                            claim_ids[check["claim_sequence"]],
                            run_id,
                            check["support_verdict"],
                            check["check_method"],
                            1 if check["quote_found"] else 0,
                            1 if check["fallback_applied"] else 0,
                            check.get("original_claim_text"),
                            check.get("detail", ""),
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"Extraction result rejected: {exc}") from exc
        ordered_claim_ids = [
            claim_ids[sequence] for sequence in sorted(claim_ids)
        ]
        return run_id, ordered_claim_ids


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def _read_output_details(
    raw_output_path: str | Path | None,
) -> tuple[str, str, int] | None:
    if raw_output_path is None:
        return None
    path = Path(raw_output_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise StoreError(f"Could not read raw extraction output: {exc}") from exc
    try:
        json.loads(data)
    except json.JSONDecodeError as exc:
        raise StoreError(f"Raw extraction output must be valid JSON: {exc}") from exc
    return str(path), hashlib.sha256(data).hexdigest(), len(data)
