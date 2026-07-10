from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import queries
from .citation import CitationChecker, resolve_claim_support, to_support_check_row
from .extraction_contract import (
    PROMPT_VERSION,
    ExtractionValidationError,
    build_extraction_prompt,
    parse_extraction_output,
    prompt_sha256,
    validate_extraction_output,
)
from .store import LedgerStore, RunRecord, utc_now


_ERROR_MESSAGE_LIMIT = 2000

Extractor = Callable[[str], "str | dict[str, Any]"]


class ExtractionPipelineError(RuntimeError):
    """Raised when an extraction attempt fails; the failed run is recorded."""

    def __init__(self, message: str, *, run_id: str | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    run_id: str
    transcript_id: str
    conversation_id: str
    status: str
    claim_ids: tuple[str, ...]
    support_verdicts: tuple[tuple[str, int], ...]
    fallback_count: int
    raw_output_path: Path


def run_extraction_pipeline(
    store: LedgerStore,
    *,
    transcript_id: str,
    extractor: Extractor,
    extractor_name: str,
    model: str | None = None,
    checker: CitationChecker | None = None,
    output_dir: str | Path = Path("data"),
) -> ExtractionOutcome:
    """Run one transcript through extraction, validation, support check, and
    raw-output write, and ledger write.

    The extractor receives the rendered S1-009 prompt and returns raw output
    (JSON text or an already-parsed object), so tests and the CLI can supply
    file-backed or mocked extractors instead of a live model call. Malformed
    output records a failed extraction run and raises ExtractionPipelineError
    instead of silently writing bad ledger rows. Each parseable response is
    saved as one JSON document under `<output_dir>/extractions/` and linked to
    its extraction run, including contract-rejected responses.
    """
    transcript_row = queries.get_transcript(store, transcript_id=transcript_id)
    transcript = _reconstruct_transcript(store, transcript_row)
    messages_by_index = {
        message["index"]: message for message in transcript["messages"]
    }

    prompt = build_extraction_prompt(transcript)
    started_at = utc_now()

    def record_failure(
        error_message: str, *, raw_output_path: Path | None = None
    ) -> str:
        run_id, _ = store.record_extraction_result(
            run=RunRecord(
                transcript_id=transcript_id,
                extractor_name=extractor_name,
                prompt_version=PROMPT_VERSION,
                prompt_sha256=prompt_sha256(prompt),
                model=model,
                status="failed",
                error_message=error_message[:_ERROR_MESSAGE_LIMIT],
                input_message_count=len(transcript["messages"]),
                output_claim_count=0,
                started_at=started_at,
                completed_at=utc_now(),
            ),
            raw_output_path=raw_output_path,
        )
        return run_id

    try:
        raw_output = extractor(prompt)
    except Exception as exc:
        run_id = record_failure(f"Extractor raised {type(exc).__name__}: {exc}")
        raise ExtractionPipelineError(
            f"Extraction failed (recorded as run {run_id}): {exc}", run_id=run_id
        ) from exc

    try:
        payload = parse_extraction_output(raw_output)
    except ExtractionValidationError as exc:
        run_id = record_failure(str(exc))
        raise ExtractionPipelineError(
            f"Extraction output was rejected (recorded as run {run_id}): {exc}",
            run_id=run_id,
        ) from exc

    try:
        raw_output_path = _write_raw_extraction_output(
            raw_output, payload=payload, output_dir=output_dir, transcript_id=transcript_id
        )
    except OSError as exc:
        run_id = record_failure(f"Could not store raw extraction JSON: {exc}")
        raise ExtractionPipelineError(
            f"Extraction output could not be stored (recorded as run {run_id}): {exc}",
            run_id=run_id,
        ) from exc

    try:
        claims = validate_extraction_output(payload, transcript)
    except ExtractionValidationError as exc:
        run_id = record_failure(str(exc), raw_output_path=raw_output_path)
        raise ExtractionPipelineError(
            f"Extraction output was rejected (recorded as run {run_id}): {exc}",
            run_id=run_id,
        ) from exc

    ledger_rows = []
    check_rows = []
    fallback_count = 0
    verdict_counts: dict[str, int] = {}
    for sequence, claim in enumerate(claims):
        source_message = messages_by_index[claim.source_message_index]
        source_text = source_message["text"]
        if claim.source_block_index is not None:
            source_text = source_message["blocks"][claim.source_block_index]["text"]
        supported = resolve_claim_support(claim, source_text, checker=checker)
        verdict_counts[supported.check.verdict] = (
            verdict_counts.get(supported.check.verdict, 0) + 1
        )
        fallback_count += 1 if supported.fallback_applied else 0
        ledger_rows.append(
            supported.claim.to_ledger_row(
                transcript_id=transcript_id,
                conversation_id=transcript_row["conversation_id"],
                claim_sequence=sequence,
                source_timestamp=source_message.get("timestamp"),
            )
        )
        check_rows.append(to_support_check_row(supported, claim_sequence=sequence))

    run_id, claim_ids = store.record_extraction_result(
        run=RunRecord(
            transcript_id=transcript_id,
            extractor_name=extractor_name,
            prompt_version=PROMPT_VERSION,
            prompt_sha256=prompt_sha256(prompt),
            model=model,
            status="succeeded",
            input_message_count=len(transcript["messages"]),
            output_claim_count=len(claims),
            started_at=started_at,
            completed_at=utc_now(),
        ),
        claims=ledger_rows,
        support_checks=check_rows,
        raw_output_path=raw_output_path,
    )
    return ExtractionOutcome(
        run_id=run_id,
        transcript_id=transcript_id,
        conversation_id=transcript_row["conversation_id"],
        status="succeeded",
        claim_ids=tuple(claim_ids),
        support_verdicts=tuple(sorted(verdict_counts.items())),
        fallback_count=fallback_count,
        raw_output_path=raw_output_path,
    )


def make_file_extractor(path: str | Path) -> Extractor:
    """Extractor that returns pre-generated output from a local JSON file.

    This is the Sprint 1 stand-in for a model-backed extractor: it lets the
    full pipeline run end to end on fixture output without a live model call.
    """

    def extractor(prompt: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    return extractor


def _write_raw_extraction_output(
    raw_output: str | dict[str, Any],
    *,
    payload: dict[str, Any],
    output_dir: str | Path,
    transcript_id: str,
) -> Path:
    """Write one complete, inspectable JSON response document for a run."""
    directory = Path(output_dir) / "extractions"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{transcript_id}-{uuid.uuid4()}.json"
    contents = (
        raw_output.strip()
        if isinstance(raw_output, str)
        else json.dumps(payload, indent=2, ensure_ascii=False)
    )
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(contents + "\n", encoding="utf-8")
    temporary_path.replace(path)
    return path


def _reconstruct_transcript(
    store: LedgerStore, transcript_row: dict[str, Any]
) -> dict[str, Any]:
    transcript_id = transcript_row["transcript_id"]
    messages = queries.list_transcript_messages(store, transcript_id=transcript_id)
    blocks_by_message: dict[int, list[dict[str, Any]]] = {}
    for block in queries.list_message_blocks(store, transcript_id=transcript_id):
        blocks_by_message.setdefault(block["message_index"], []).append(
            {"type": block["block_type"], "text": block["text"]}
        )
    return {
        "conversation": {
            "id": transcript_row["conversation_id"],
            "title": transcript_row["title"],
        },
        "messages": [
            {
                "index": message["message_index"],
                "role": message["role"],
                "text": message["text"],
                "timestamp": message["timestamp"],
                "blocks": blocks_by_message.get(message["message_index"], []),
            }
            for message in messages
        ],
    }
