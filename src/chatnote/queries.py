from __future__ import annotations

from typing import Any

from .store import LedgerStore, SPEAKER_ROLES, SPEECH_ACT_TYPES, SUPPORT_VERDICTS


class QueryError(ValueError):
    """Raised when a query receives an unsupported filter value."""


_CLAIM_COLUMNS = """
    claim_id, run_id, transcript_id, conversation_id, claim_sequence,
    standalone_claim_text, speaker_role, speaker_label, speech_act_type,
    hedge_level, source_message_index, source_block_index, source_char_start,
    source_char_end, source_quote, source_timestamp, concept_tags_json,
    supersedes_claim_id, created_at
"""


def list_transcripts(
    store: LedgerStore, *, conversation_id: str | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT transcript_id, conversation_id, title, source_artifact_id, "
        "transcript_artifact_id, parser_method, fetched_at, message_count, "
        "warning_count, created_at FROM transcripts"
    )
    params: list[Any] = []
    if conversation_id is not None:
        sql += " WHERE conversation_id = ?"
        params.append(conversation_id)
    sql += " ORDER BY fetched_at, transcript_id"
    return _rows(store, sql, params)


def get_transcript(store: LedgerStore, *, transcript_id: str) -> dict[str, Any]:
    rows = _rows(
        store,
        "SELECT transcript_id, conversation_id, title, source_artifact_id, "
        "transcript_artifact_id, parser_method, fetched_at, message_count, "
        "warning_count, created_at FROM transcripts WHERE transcript_id = ?",
        [transcript_id],
    )
    if not rows:
        raise QueryError(f"Unknown transcript_id: {transcript_id!r}")
    return rows[0]


def list_transcript_messages(
    store: LedgerStore, *, transcript_id: str
) -> list[dict[str, Any]]:
    return _rows(
        store,
        "SELECT transcript_id, conversation_id, message_index, role, text, "
        "timestamp, provenance_json FROM transcript_messages "
        "WHERE transcript_id = ? ORDER BY message_index",
        [transcript_id],
    )


def list_message_blocks(
    store: LedgerStore, *, transcript_id: str
) -> list[dict[str, Any]]:
    return _rows(
        store,
        "SELECT transcript_id, message_index, block_index, block_type, text, "
        "language, metadata_json FROM transcript_message_blocks "
        "WHERE transcript_id = ? ORDER BY message_index, block_index",
        [transcript_id],
    )


def list_claims(
    store: LedgerStore,
    *,
    conversation_id: str | None = None,
    speaker_role: str | None = None,
    speech_act_type: str | None = None,
    transcript_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return ledger claims filtered by conversation, speaker, and speech act.

    Every result row keeps the full source pointer (transcript_id,
    source_message_index, optional block/char offsets, and source_quote).
    """
    if speaker_role is not None and speaker_role not in SPEAKER_ROLES:
        raise QueryError(
            f"Unsupported speaker role: {speaker_role!r}. "
            f"Expected one of: {', '.join(SPEAKER_ROLES)}."
        )
    if speech_act_type is not None and speech_act_type not in SPEECH_ACT_TYPES:
        raise QueryError(
            f"Unsupported speech-act type: {speech_act_type!r}. "
            f"Expected one of: {', '.join(SPEECH_ACT_TYPES)}."
        )

    sql = f"SELECT {_CLAIM_COLUMNS} FROM claim_ledger"
    filters = []
    params: list[Any] = []
    for column, value in (
        ("conversation_id", conversation_id),
        ("speaker_role", speaker_role),
        ("speech_act_type", speech_act_type),
        ("transcript_id", transcript_id),
    ):
        if value is not None:
            filters.append(f"{column} = ?")
            params.append(value)
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY created_at, run_id, claim_sequence"
    return _rows(store, sql, params)


def get_claim_source(store: LedgerStore, *, claim_id: str) -> dict[str, Any]:
    """Trace one claim back to its source message (and block when pointed at)."""
    claims = _rows(
        store,
        f"SELECT {_CLAIM_COLUMNS} FROM claim_ledger WHERE claim_id = ?",
        [claim_id],
    )
    if not claims:
        raise QueryError(f"Unknown claim_id: {claim_id!r}")
    claim = claims[0]
    messages = _rows(
        store,
        "SELECT message_index, role, text, timestamp, provenance_json "
        "FROM transcript_messages WHERE transcript_id = ? AND message_index = ?",
        [claim["transcript_id"], claim["source_message_index"]],
    )
    result = {"claim": claim, "source_message": messages[0] if messages else None}
    if claim["source_block_index"] is not None:
        blocks = _rows(
            store,
            "SELECT block_index, block_type, text, language, metadata_json "
            "FROM transcript_message_blocks "
            "WHERE transcript_id = ? AND message_index = ? AND block_index = ?",
            [
                claim["transcript_id"],
                claim["source_message_index"],
                claim["source_block_index"],
            ],
        )
        result["source_block"] = blocks[0] if blocks else None
    return result


def list_extraction_runs(
    store: LedgerStore, *, transcript_id: str | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT run_id, transcript_id, extractor_name, prompt_version, "
        "prompt_sha256, model, status, error_message, input_message_count, "
        "output_claim_count, started_at, completed_at, created_at "
        "FROM extraction_runs"
    )
    params: list[Any] = []
    if transcript_id is not None:
        sql += " WHERE transcript_id = ?"
        params.append(transcript_id)
    sql += " ORDER BY started_at, run_id"
    return _rows(store, sql, params)


def list_support_checks(
    store: LedgerStore,
    *,
    claim_id: str | None = None,
    run_id: str | None = None,
    support_verdict: str | None = None,
) -> list[dict[str, Any]]:
    if support_verdict is not None and support_verdict not in SUPPORT_VERDICTS:
        raise QueryError(
            f"Unsupported support verdict: {support_verdict!r}. "
            f"Expected one of: {', '.join(SUPPORT_VERDICTS)}."
        )
    sql = (
        "SELECT check_id, claim_id, run_id, support_verdict, check_method, "
        "quote_found, fallback_applied, original_claim_text, detail, created_at "
        "FROM claim_support_checks"
    )
    filters = []
    params: list[Any] = []
    for column, value in (
        ("claim_id", claim_id),
        ("run_id", run_id),
        ("support_verdict", support_verdict),
    ):
        if value is not None:
            filters.append(f"{column} = ?")
            params.append(value)
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY created_at, check_id"
    return _rows(store, sql, params)


def _rows(store: LedgerStore, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cursor = store.connection.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]
