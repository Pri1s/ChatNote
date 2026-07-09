#!/usr/bin/env python3
"""One-file entry point for the whole ChatNote flow.

Loads .env, then runs share link -> capture -> store ingest -> claim
extraction -> prints the extracted claims, so no separate terminal commands or
exports are needed:

    python3 main.py https://claude.ai/share/<id>

With no arguments it prompts for the share URL. Extra CLI flags are forwarded
to `chatnote run` (e.g. --model, --claims-json, --db-path, --output-dir), so
this wrapper adds nothing the CLI can't do — it only saves the typing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from chatnote.cli import main as chatnote_cli  # noqa: E402
from chatnote.url_validation import (  # noqa: E402
    URLValidationError,
    validate_claude_share_url,
)


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from .env without overriding real environment."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env")

    args = list(sys.argv[1:] if argv is None else argv)
    if args and not args[0].startswith("-"):
        url, extra = args[0], args[1:]
    else:
        extra = args
        try:
            url = input("Claude share URL (https://claude.ai/share/<id>): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
    if not url:
        print("chatnote: error: no share URL given.", file=sys.stderr)
        return 1

    exit_code = chatnote_cli(["run", url, *extra])
    if exit_code != 0:
        return exit_code

    try:
        conversation_id = validate_claude_share_url(url).conversation_id
    except URLValidationError:
        return 0
    query_args = ["query", "claims", "--conversation", conversation_id]
    if "--db-path" in extra:
        db_path_index = extra.index("--db-path") + 1
        if db_path_index < len(extra):
            query_args += ["--db-path", extra[db_path_index]]
    print()
    print("Extracted claims:")
    return chatnote_cli(query_args)


if __name__ == "__main__":
    raise SystemExit(main())
