from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .capture import FetchError, capture_share_link
from .parser import ParseError
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "capture":
        return _capture_command(args)

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
