from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import queries
from .capture import FetchError, capture_share_link
from .parser import ParseError
from .pipeline import ExtractionPipelineError, make_file_extractor, run_extraction_pipeline
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
        required=True,
        help="File containing extraction output JSON (the Sprint 1 file-backed extractor).",
    )
    extract.add_argument(
        "--extractor-name",
        default="file-extractor",
        help="Extractor name recorded on the extraction run.",
    )
    extract.add_argument("--model", help="Optional model name recorded on the extraction run.")
    _add_db_path_argument(extract)

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


def _extract_command(args: argparse.Namespace) -> int:
    try:
        with LedgerStore.open(args.db_path) as store:
            outcome = run_extraction_pipeline(
                store,
                transcript_id=args.transcript_id,
                extractor=make_file_extractor(args.claims_json),
                extractor_name=args.extractor_name,
                model=args.model,
            )
    except ExtractionPipelineError as exc:
        print(f"chatnote: error: {exc}", file=sys.stderr)
        return 1
    except (StoreError, queries.QueryError) as exc:
        print(f"chatnote: error: {exc}", file=sys.stderr)
        return 1

    print(f"Extraction run: {outcome.run_id}")
    print(f"Status: {outcome.status}")
    print(f"Claims written: {len(outcome.claim_ids)}")
    for verdict, count in outcome.support_verdicts:
        print(f"Citation support {verdict}: {count}")
    print(f"Quote fallbacks applied: {outcome.fallback_count}")
    return 0


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
