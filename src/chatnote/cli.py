from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import queries
from .capture import FetchError, capture_share_link
from .llm_extractor import (
    LLMExtractorError,
    build_openrouter_extractor,
    load_openrouter_config,
)
from .parser import ParseError
from .pipeline import (
    ExtractionPipelineError,
    Extractor,
    make_file_extractor,
    run_extraction_pipeline,
)
from .store import (
    DEFAULT_DB_PATH,
    LedgerStore,
    SPEAKER_ROLES,
    SPEECH_ACT_TYPES,
    StoreError,
)
from .url_validation import URLValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chatnote")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser(
        "capture",
        help="Capture a public Claude shared chat snapshot into raw HTML and transcript JSON.",
    )
    capture.add_argument("url", help="Claude shared snapshot URL, e.g. https://claude.ai/share/<id>")
    capture.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data"),
        help="Directory for local capture outputs. Defaults to ./data.",
    )

    store = subparsers.add_parser(
        "store",
        help="Initialize and fill the local raw store and claim ledger database.",
    )
    store_subparsers = store.add_subparsers(dest="store_command", required=True)

    store_init = store_subparsers.add_parser(
        "init", help="Create the local SQLite store from the schema contract."
    )
    _add_db_path_argument(store_init)

    store_ingest = store_subparsers.add_parser(
        "ingest",
        help="Register one captured raw source and transcript JSON pair in the store.",
    )
    store_ingest.add_argument("raw_path", type=Path, help="Raw snapshot file from data/raw/.")
    store_ingest.add_argument(
        "transcript_path", type=Path, help="Parsed transcript JSON from data/transcripts/."
    )
    _add_db_path_argument(store_ingest)

    extract = subparsers.add_parser(
        "extract",
        help="Run one stored transcript through the claim extraction pipeline.",
    )
    extract.add_argument("transcript_id", help="Transcript ID from `chatnote store ingest`.")
    extract.add_argument(
        "--claims-json",
        type=Path,
        help="File containing pre-generated extraction output JSON (the offline "
        "file-backed extractor). When omitted, claims are extracted with the "
        "OpenRouter model from --model or CHATNOTE_MODEL.",
    )
    extract.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data"),
        help="Directory for stored raw extraction JSON. Defaults to ./data.",
    )
    extract.add_argument(
        "--extractor-name",
        help="Extractor name recorded on the extraction run. Defaults to "
        "file-extractor with --claims-json, openrouter otherwise.",
    )
    extract.add_argument(
        "--model",
        help="Model for the OpenRouter extractor (falls back to CHATNOTE_MODEL); "
        "with --claims-json it is only recorded on the extraction run.",
    )
    _add_db_path_argument(extract)

    run = subparsers.add_parser(
        "run",
        help="Capture a share link, ingest it, and extract claims in one step.",
    )
    run.add_argument("url", help="Claude shared snapshot URL, e.g. https://claude.ai/share/<id>")
    run.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data"),
        help="Directory for local capture outputs. Defaults to ./data.",
    )
    run_extractor = run.add_mutually_exclusive_group()
    run_extractor.add_argument(
        "--model",
        help="Model for the OpenRouter extractor (falls back to CHATNOTE_MODEL).",
    )
    run_extractor.add_argument(
        "--claims-json",
        type=Path,
        help="Use pre-generated extraction output JSON instead of a model call.",
    )
    _add_db_path_argument(run)

    query = subparsers.add_parser(
        "query", help="Inspect stored transcripts, claims, and extraction runs."
    )
    query_subparsers = query.add_subparsers(dest="query_command", required=True)

    query_transcripts = query_subparsers.add_parser(
        "transcripts", help="List stored transcripts."
    )
    query_transcripts.add_argument("--conversation", help="Filter by conversation ID.")
    _add_db_path_argument(query_transcripts)
    _add_json_argument(query_transcripts)

    query_claims = query_subparsers.add_parser("claims", help="List claim ledger records.")
    query_claims.add_argument("--conversation", help="Filter by conversation ID.")
    query_claims.add_argument(
        "--speaker", choices=SPEAKER_ROLES, help="Filter by speaker role."
    )
    query_claims.add_argument(
        "--speech-act", choices=SPEECH_ACT_TYPES, help="Filter by speech-act type."
    )
    _add_db_path_argument(query_claims)
    _add_json_argument(query_claims)

    query_runs = query_subparsers.add_parser("runs", help="List extraction runs.")
    query_runs.add_argument("--transcript-id", help="Filter by transcript ID.")
    _add_db_path_argument(query_runs)
    _add_json_argument(query_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "capture":
        return _capture_command(args)
    if args.command == "store":
        return _store_command(args)
    if args.command == "extract":
        return _extract_command(args)
    if args.command == "run":
        return _run_command(args)
    if args.command == "query":
        return _query_command(args)

    parser.error(f"Unsupported command: {args.command}")
    return 2


def _capture_command(args: argparse.Namespace, *, fetcher=None) -> int:
    try:
        result = capture_share_link(
            args.url,
            output_dir=args.output_dir,
            fetcher=fetcher or None,
        )
    except (URLValidationError, FetchError, ParseError) as exc:
        print(f"chatnote: error: {exc}", file=sys.stderr)
        return 1

    print(f"Captured Claude conversation: {result.conversation_id}")
    if result.title:
        print(f"Title: {result.title}")
    print(f"Messages: {result.message_count}")
    print(f"Warnings: {result.warning_count}")
    print(f"Raw source: {result.raw_path}")
    print(f"Transcript JSON: {result.transcript_path}")
    return 0


def _store_command(args: argparse.Namespace) -> int:
    try:
        with LedgerStore.open(args.db_path) as store:
            if args.store_command == "init":
                print(f"Initialized ChatNote store: {args.db_path}")
                return 0
            result = store.ingest_capture(
                raw_path=args.raw_path, transcript_path=args.transcript_path
            )
    except StoreError as exc:
        print(f"chatnote: error: {exc}", file=sys.stderr)
        return 1

    print(f"Ingested conversation: {result.conversation_id}")
    print(f"Transcript ID: {result.transcript_id}")
    print(f"Messages: {result.message_count}")
    print(f"Warnings: {result.warning_count}")
    return 0


def _resolve_extractor(
    args: argparse.Namespace, *, transport=None
) -> tuple[Extractor, str, str | None]:
    """Pick the file-backed or OpenRouter extractor from CLI arguments.

    Raises LLMExtractorError on missing model/key configuration, before any
    extraction run row is written.
    """
    extractor_name = getattr(args, "extractor_name", None)
    if args.claims_json is not None:
        return (
            make_file_extractor(args.claims_json),
            extractor_name or "file-extractor",
            args.model,
        )
    config = load_openrouter_config(model=args.model)
    return (
        build_openrouter_extractor(config, transport=transport),
        extractor_name or "openrouter",
        config.model,
    )


def _extract_command(args: argparse.Namespace, *, transport=None) -> int:
    try:
        extractor, extractor_name, model = _resolve_extractor(args, transport=transport)
    except LLMExtractorError as exc:
        print(f"chatnote: error: {exc}", file=sys.stderr)
        return 1

    try:
        with LedgerStore.open(args.db_path) as store:
            outcome = run_extraction_pipeline(
                store,
                transcript_id=args.transcript_id,
                extractor=extractor,
                extractor_name=extractor_name,
                model=model,
                output_dir=getattr(args, "output_dir", Path("data")),
            )
    except ExtractionPipelineError as exc:
        print(f"chatnote: error: {exc}", file=sys.stderr)
        return 1
    except (StoreError, queries.QueryError) as exc:
        print(f"chatnote: error: {exc}", file=sys.stderr)
        return 1

    _print_extraction_outcome(outcome)
    return 0


def _run_command(
    args: argparse.Namespace, *, fetcher=None, transport=None, extractor=None
) -> int:
    extractor_name = "openrouter"
    model = args.model
    if extractor is None:
        try:
            extractor, extractor_name, model = _resolve_extractor(
                args, transport=transport
            )
        except LLMExtractorError as exc:
            print(f"chatnote: error: {exc}", file=sys.stderr)
            return 1

    try:
        capture = capture_share_link(
            args.url, output_dir=args.output_dir, fetcher=fetcher or None
        )
    except (URLValidationError, FetchError, ParseError) as exc:
        print(f"chatnote: error: capture failed: {exc}", file=sys.stderr)
        return 1

    print(f"Captured Claude conversation: {capture.conversation_id}")
    if capture.title:
        print(f"Title: {capture.title}")
    print(f"Messages: {capture.message_count}")
    print(f"Raw source: {capture.raw_path}")
    print(f"Transcript JSON: {capture.transcript_path}")

    try:
        with LedgerStore.open(args.db_path) as store:
            try:
                ingest = store.ingest_capture(
                    raw_path=capture.raw_path, transcript_path=capture.transcript_path
                )
            except StoreError as exc:
                print(f"chatnote: error: ingest failed: {exc}", file=sys.stderr)
                print(
                    "Capture outputs were written and can be ingested separately "
                    f"with: chatnote store ingest {capture.raw_path} "
                    f"{capture.transcript_path}",
                    file=sys.stderr,
                )
                return 1
            print(f"Transcript ID: {ingest.transcript_id}")

            try:
                outcome = run_extraction_pipeline(
                    store,
                    transcript_id=ingest.transcript_id,
                    extractor=extractor,
                    extractor_name=extractor_name,
                    model=model,
                    output_dir=args.output_dir,
                )
            except (ExtractionPipelineError, queries.QueryError) as exc:
                print(f"chatnote: error: extraction failed: {exc}", file=sys.stderr)
                print(
                    f"The transcript is stored as {ingest.transcript_id}; inspect "
                    "failed runs with: chatnote query runs --transcript-id "
                    f"{ingest.transcript_id} --db-path {args.db_path}",
                    file=sys.stderr,
                )
                return 1
    except StoreError as exc:
        print(f"chatnote: error: {exc}", file=sys.stderr)
        return 1

    _print_extraction_outcome(outcome)
    print("Next:")
    print(
        f"  chatnote query claims --conversation {capture.conversation_id} "
        f"--db-path {args.db_path}"
    )
    print(
        f"  chatnote query runs --transcript-id {ingest.transcript_id} "
        f"--db-path {args.db_path}"
    )
    return 0


def _print_extraction_outcome(outcome) -> None:
    print(f"Extraction run: {outcome.run_id}")
    print(f"Status: {outcome.status}")
    print(f"Claims written: {len(outcome.claim_ids)}")
    print(f"Raw LLM JSON: {outcome.raw_output_path}")
    for verdict, count in outcome.support_verdicts:
        print(f"Citation support {verdict}: {count}")
    print(f"Quote fallbacks applied: {outcome.fallback_count}")


def _query_command(args: argparse.Namespace) -> int:
    try:
        with LedgerStore.open(args.db_path) as store:
            if args.query_command == "transcripts":
                rows = queries.list_transcripts(store, conversation_id=args.conversation)
            elif args.query_command == "claims":
                rows = queries.list_claims(
                    store,
                    conversation_id=args.conversation,
                    speaker_role=args.speaker,
                    speech_act_type=args.speech_act,
                )
            else:
                rows = queries.list_extraction_runs(
                    store, transcript_id=args.transcript_id
                )
    except (StoreError, queries.QueryError) as exc:
        print(f"chatnote: error: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("No records found.")
        return 0
    printer = {
        "transcripts": _print_transcript,
        "claims": _print_claim,
        "runs": _print_run,
    }[args.query_command]
    for row in rows:
        printer(row)
    return 0


def _print_transcript(row: dict) -> None:
    title = row["title"] or "(no title)"
    print(f"{row['transcript_id']}  {row['conversation_id']}  {title}")
    print(
        f"  parser: {row['parser_method']}  fetched: {row['fetched_at']}  "
        f"messages: {row['message_count']}  warnings: {row['warning_count']}"
    )


def _print_claim(row: dict) -> None:
    print(f"{row['claim_id']}")
    print(
        f"  speaker: {row['speaker_role']}  speech-act: {row['speech_act_type']}  "
        f"hedge: {row['hedge_level']}"
    )
    print(f"  claim: {row['standalone_claim_text']}")
    pointer = f"transcript {row['transcript_id']} message {row['source_message_index']}"
    if row["source_block_index"] is not None:
        pointer += f" block {row['source_block_index']}"
    if row["source_char_start"] is not None:
        pointer += f" chars [{row['source_char_start']}, {row['source_char_end']})"
    print(f"  source: {pointer}")
    print(f"  quote: {row['source_quote']}")


def _print_run(row: dict) -> None:
    print(f"{row['run_id']}  {row['transcript_id']}  {row['status']}")
    print(
        f"  extractor: {row['extractor_name']}  prompt: {row['prompt_version']}  "
        f"claims: {row['output_claim_count']}"
    )
    if row["error_message"]:
        print(f"  error: {row['error_message']}")
    if row["raw_output_path"]:
        print(f"  raw LLM JSON: {row['raw_output_path']}")


def _add_db_path_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite store path. Defaults to {DEFAULT_DB_PATH}.",
    )


def _add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print results as JSON instead of human-readable lines.",
    )
