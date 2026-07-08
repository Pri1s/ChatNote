from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from chatnote.cli import _capture_command, main


FIXTURE_DIR = Path(__file__).parent / "fixtures"
RAW_FIXTURE = FIXTURE_DIR / "ledger_raw_snapshot.json"
TRANSCRIPT_FIXTURE = FIXTURE_DIR / "ledger_transcript.json"
VALID_OUTPUT_FIXTURE = FIXTURE_DIR / "extraction_output_valid.json"


def run_cli(*argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = main(list(argv))
    return exit_code, stdout.getvalue(), stderr.getvalue()


class CLITests(unittest.TestCase):
    def test_capture_command_writes_raw_and_transcript(self) -> None:
        html = (FIXTURE_DIR / "claude_share_structured.html").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(
                command="capture",
                url="https://claude.ai/share/fixture-structured",
                output_dir=Path(tmpdir),
            )
            exit_code = _capture_command(args, fetcher=lambda url: html)

            self.assertEqual(exit_code, 0)
            raw_files = list((Path(tmpdir) / "raw").glob("*.html"))
            transcript_files = list((Path(tmpdir) / "transcripts").glob("*.json"))
            self.assertEqual(len(raw_files), 1)
            self.assertEqual(len(transcript_files), 1)

            transcript = json.loads(transcript_files[0].read_text(encoding="utf-8"))
            self.assertEqual(transcript["conversation"]["id"], "fixture-structured")
            self.assertEqual(len(transcript["messages"]), 2)

    def test_capture_command_writes_json_raw_when_fetcher_returns_api_payload(self) -> None:
        payload = (FIXTURE_DIR / "claude_share_api_snapshot.json").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(
                command="capture",
                url="https://claude.ai/share/fixture-api",
                output_dir=Path(tmpdir),
            )
            exit_code = _capture_command(args, fetcher=lambda url: payload)

            self.assertEqual(exit_code, 0)
            raw_files = list((Path(tmpdir) / "raw").glob("*.json"))
            transcript_files = list((Path(tmpdir) / "transcripts").glob("*.json"))
            self.assertEqual(len(raw_files), 1)
            self.assertEqual(len(transcript_files), 1)

            transcript = json.loads(transcript_files[0].read_text(encoding="utf-8"))
            self.assertEqual(transcript["conversation"]["title"], "API Snapshot Fixture")
            self.assertEqual(len(transcript["messages"]), 2)


class StoreAndExtractCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = str(Path(self._tmpdir.name) / "chatnote.db")

    def _ingest_fixture(self) -> str:
        exit_code, stdout, _ = run_cli(
            "store",
            "ingest",
            str(RAW_FIXTURE),
            str(TRANSCRIPT_FIXTURE),
            "--db-path",
            self.db_path,
        )
        self.assertEqual(exit_code, 0)
        for line in stdout.splitlines():
            if line.startswith("Transcript ID: "):
                return line.removeprefix("Transcript ID: ")
        self.fail(f"Transcript ID not found in output: {stdout!r}")

    def test_store_init_creates_database(self) -> None:
        exit_code, stdout, _ = run_cli("store", "init", "--db-path", self.db_path)
        self.assertEqual(exit_code, 0)
        self.assertIn("Initialized ChatNote store", stdout)
        self.assertTrue(Path(self.db_path).is_file())

    def test_ingest_extract_and_query_round_trip(self) -> None:
        transcript_id = self._ingest_fixture()

        exit_code, stdout, _ = run_cli(
            "extract",
            transcript_id,
            "--claims-json",
            str(VALID_OUTPUT_FIXTURE),
            "--db-path",
            self.db_path,
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("Status: succeeded", stdout)
        self.assertIn("Claims written: 3", stdout)

        exit_code, stdout, _ = run_cli(
            "query", "claims", "--db-path", self.db_path, "--json"
        )
        self.assertEqual(exit_code, 0)
        claims = json.loads(stdout)
        self.assertEqual(len(claims), 3)
        self.assertEqual(claims[0]["conversation_id"], "ledger-fixture-conv")

        exit_code, stdout, _ = run_cli(
            "query",
            "claims",
            "--speaker",
            "assistant",
            "--db-path",
            self.db_path,
            "--json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(json.loads(stdout)), 1)

        exit_code, stdout, _ = run_cli(
            "query", "transcripts", "--db-path", self.db_path
        )
        self.assertEqual(exit_code, 0)
        self.assertIn(transcript_id, stdout)

    def test_extract_with_malformed_output_reports_failed_run(self) -> None:
        transcript_id = self._ingest_fixture()
        bad_claims = Path(self._tmpdir.name) / "bad_claims.json"
        bad_claims.write_text("not json", encoding="utf-8")

        exit_code, _, stderr = run_cli(
            "extract",
            transcript_id,
            "--claims-json",
            str(bad_claims),
            "--db-path",
            self.db_path,
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("chatnote: error", stderr)

        exit_code, stdout, _ = run_cli(
            "query", "runs", "--db-path", self.db_path, "--json"
        )
        self.assertEqual(exit_code, 0)
        runs = json.loads(stdout)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("not valid JSON", runs[0]["error_message"])

    def test_query_claims_with_no_records(self) -> None:
        exit_code, stdout, _ = run_cli("query", "claims", "--db-path", self.db_path)
        self.assertEqual(exit_code, 0)
        self.assertIn("No records found.", stdout)

    def test_ingest_missing_file_reports_error(self) -> None:
        exit_code, _, stderr = run_cli(
            "store",
            "ingest",
            str(Path(self._tmpdir.name) / "missing.json"),
            str(TRANSCRIPT_FIXTURE),
            "--db-path",
            self.db_path,
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("chatnote: error", stderr)


if __name__ == "__main__":
    unittest.main()
