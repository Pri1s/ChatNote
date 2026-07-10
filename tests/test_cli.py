from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chatnote.capture import FetchError
from chatnote.cli import _capture_command, _extract_command, _run_command, main


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
            "--output-dir",
            str(Path(self._tmpdir.name) / "data"),
            "--db-path",
            self.db_path,
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("Status: succeeded", stdout)
        self.assertIn("Claims written: 3", stdout)
        self.assertIn("Raw LLM JSON:", stdout)

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

    def test_extract_without_claims_json_reports_missing_llm_config(self) -> None:
        transcript_id = self._ingest_fixture()
        with mock.patch.dict(os.environ, {}, clear=True):
            exit_code, _, stderr = run_cli(
                "extract", transcript_id, "--db-path", self.db_path
            )
        self.assertEqual(exit_code, 1)
        self.assertIn("chatnote: error", stderr)
        self.assertIn("CHATNOTE_MODEL", stderr)

        exit_code, stdout, _ = run_cli(
            "query", "runs", "--db-path", self.db_path, "--json"
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout), [])

    def test_extract_without_claims_json_uses_openrouter_extractor(self) -> None:
        transcript_id = self._ingest_fixture()
        content = VALID_OUTPUT_FIXTURE.read_text(encoding="utf-8")
        requests: list[dict] = []

        def transport(request: dict) -> dict:
            requests.append(request)
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ]
            }

        args = argparse.Namespace(
            command="extract",
            transcript_id=transcript_id,
            claims_json=None,
            extractor_name=None,
            model="test/model",
            db_path=Path(self.db_path),
            output_dir=Path(self._tmpdir.name) / "data",
        )
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "key"}, clear=True):
            with contextlib.redirect_stdout(stdout):
                exit_code = _extract_command(args, transport=transport)

        self.assertEqual(exit_code, 0)
        self.assertIn("Claims written: 3", stdout.getvalue())
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["body"]["model"], "test/model")

        exit_code, runs_json, _ = run_cli(
            "query", "runs", "--db-path", self.db_path, "--json"
        )
        self.assertEqual(exit_code, 0)
        run = json.loads(runs_json)[0]
        self.assertEqual(run["extractor_name"], "openrouter")
        self.assertEqual(run["model"], "test/model")
        self.assertTrue(Path(run["raw_output_path"]).is_file())

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


class RunCommandTests(unittest.TestCase):
    STRUCTURED_CLAIMS = {
        "claims": [
            {
                "standalone_claim_text": "The user asked for a tiny Python example.",
                "speaker_role": "user",
                "speech_act_type": "question",
                "hedge_level": "none",
                "source_message_index": 0,
                "source_quote": "Can you show me a tiny Python example?",
                "source_block_index": None,
                "concept_tags": None,
            }
        ]
    }

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.output_dir = Path(self._tmpdir.name) / "data"
        self.db_path = Path(self._tmpdir.name) / "chatnote.db"
        self.html = (FIXTURE_DIR / "claude_share_structured.html").read_text(
            encoding="utf-8"
        )

    def _run_args(self, **overrides) -> argparse.Namespace:
        defaults = dict(
            command="run",
            url="https://claude.ai/share/fixture-structured",
            output_dir=self.output_dir,
            model=None,
            claims_json=None,
            db_path=self.db_path,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def _invoke(self, args, **kwargs) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = _run_command(args, **kwargs)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_run_chains_capture_ingest_and_extract(self) -> None:
        exit_code, stdout, _ = self._invoke(
            self._run_args(),
            fetcher=lambda url: self.html,
            extractor=lambda prompt: self.STRUCTURED_CLAIMS,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("Captured Claude conversation: fixture-structured", stdout)
        self.assertIn("Transcript ID: ", stdout)
        self.assertIn("Status: succeeded", stdout)
        self.assertIn("Claims written: 1", stdout)
        self.assertIn("Citation support partial: 1", stdout)
        self.assertIn("Raw LLM JSON:", stdout)
        self.assertIn("chatnote query claims --conversation fixture-structured", stdout)

        exit_code, claims_json, _ = run_cli(
            "query", "claims", "--db-path", str(self.db_path), "--json"
        )
        self.assertEqual(exit_code, 0)
        claims = json.loads(claims_json)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["conversation_id"], "fixture-structured")
        exit_code, runs_json, _ = run_cli(
            "query", "runs", "--db-path", str(self.db_path), "--json"
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(Path(json.loads(runs_json)[0]["raw_output_path"]).is_file())

    def test_run_stubbed_transport_covers_llm_path(self) -> None:
        content = json.dumps(self.STRUCTURED_CLAIMS)

        def transport(request: dict) -> dict:
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ]
            }

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "key"}, clear=True):
            exit_code, stdout, _ = self._invoke(
                self._run_args(model="test/model"),
                fetcher=lambda url: self.html,
                transport=transport,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Claims written: 1", stdout)
        exit_code, runs_json, _ = run_cli(
            "query", "runs", "--db-path", str(self.db_path), "--json"
        )
        run = json.loads(runs_json)[0]
        self.assertEqual(run["extractor_name"], "openrouter")
        self.assertEqual(run["model"], "test/model")

    def test_run_reports_missing_llm_config_before_capturing(self) -> None:
        fetched: list[str] = []

        def fetcher(url: str) -> str:
            fetched.append(url)
            return self.html

        with mock.patch.dict(os.environ, {}, clear=True):
            exit_code, _, stderr = self._invoke(self._run_args(), fetcher=fetcher)

        self.assertEqual(exit_code, 1)
        self.assertIn("CHATNOTE_MODEL", stderr)
        self.assertEqual(fetched, [])
        self.assertFalse(self.output_dir.exists())

    def test_run_capture_failure_exits_before_store_writes(self) -> None:
        def failing_fetcher(url: str) -> str:
            raise FetchError("network down")

        exit_code, _, stderr = self._invoke(
            self._run_args(),
            fetcher=failing_fetcher,
            extractor=lambda prompt: {"claims": []},
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("capture failed", stderr)
        self.assertFalse(self.db_path.exists())

    def test_run_extraction_failure_keeps_stored_transcript(self) -> None:
        exit_code, stdout, stderr = self._invoke(
            self._run_args(),
            fetcher=lambda url: self.html,
            extractor=lambda prompt: "not json",
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("extraction failed", stderr)
        self.assertIn("chatnote query runs --transcript-id", stderr)
        self.assertIn("Transcript ID: ", stdout)

        exit_code, runs_json, _ = run_cli(
            "query", "runs", "--db-path", str(self.db_path), "--json"
        )
        runs = json.loads(runs_json)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
