from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from chatnote.cli import _capture_command


FIXTURE_DIR = Path(__file__).parent / "fixtures"


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


if __name__ == "__main__":
    unittest.main()
