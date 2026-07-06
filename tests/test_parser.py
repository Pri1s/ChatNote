from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from chatnote.parser import ParseError, parse_transcript


FIXTURE_DIR = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_prefers_structured_messages(self) -> None:
        html = (FIXTURE_DIR / "claude_share_structured.html").read_text(encoding="utf-8")

        transcript = parse_transcript(
            html,
            source_url="https://claude.ai/share/fixture-structured",
            conversation_id="fixture-structured",
            fetched_at="2026-07-06T12:02:00Z",
            raw_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        ).to_dict()

        self.assertEqual(transcript["source"]["parser_method"], "embedded_structured_data")
        self.assertEqual(transcript["conversation"]["title"], "Fixture Structured Chat")
        self.assertEqual([message["role"] for message in transcript["messages"]], ["user", "assistant"])
        self.assertIn("```python", transcript["messages"][1]["text"])
        self.assertIn("| Key | Value |", transcript["messages"][1]["text"])
        self.assertTrue(
            any(warning["code"] == "attachment_placeholder" for warning in transcript["warnings"])
        )

    def test_rendered_html_fallback_preserves_order_code_table_and_warnings(self) -> None:
        html = (FIXTURE_DIR / "claude_share_rendered.html").read_text(encoding="utf-8")

        transcript = parse_transcript(
            html,
            source_url="https://claude.ai/share/fixture-rendered",
            conversation_id="fixture-rendered",
            fetched_at="2026-07-06T12:02:00Z",
            raw_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        ).to_dict()

        self.assertEqual(transcript["source"]["parser_method"], "rendered_html")
        self.assertEqual(transcript["conversation"]["title"], "Rendered Snapshot Fixture")
        self.assertEqual([message["role"] for message in transcript["messages"]], ["user", "assistant"])
        self.assertEqual(transcript["messages"][0]["timestamp"], None)
        self.assertIn("```python", transcript["messages"][1]["text"])
        self.assertIn("| Name | Status |", transcript["messages"][1]["text"])
        warning_codes = {warning["code"] for warning in transcript["warnings"]}
        self.assertIn("structured_data_not_found", warning_codes)
        self.assertIn("missing_timestamp", warning_codes)
        self.assertIn("attachment_placeholder", warning_codes)

    def test_parses_top_level_claude_snapshot_api_json(self) -> None:
        payload = (FIXTURE_DIR / "claude_share_api_snapshot.json").read_text(encoding="utf-8")

        transcript = parse_transcript(
            payload,
            source_url="https://claude.ai/share/fixture-api",
            conversation_id="fixture-api",
            fetched_at="2026-07-06T12:02:00Z",
            raw_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        ).to_dict()

        self.assertEqual(transcript["source"]["parser_method"], "json_structured_data")
        self.assertEqual(transcript["conversation"]["title"], "API Snapshot Fixture")
        self.assertEqual([message["role"] for message in transcript["messages"]], ["user", "assistant"])
        self.assertIn("public snapshot API payload", transcript["messages"][0]["text"])
        self.assertIn("```python", transcript["messages"][1]["text"])
        warning_codes = {warning["code"] for warning in transcript["warnings"]}
        self.assertIn("attachment_placeholder", warning_codes)
        self.assertIn("unsupported_tool_call", warning_codes)

    def test_empty_claude_app_shell_error_is_actionable(self) -> None:
        html = (
            '<!doctype html><html><head><script type="module" '
            'src="https://assets-proxy.anthropic.com/claude-ai/v2/assets/v1/index.js">'
            '</script></head><body><div id="root"></div></body></html>'
        )

        with self.assertRaisesRegex(ParseError, "web app shell"):
            parse_transcript(
                html,
                source_url="https://claude.ai/share/empty-shell",
                conversation_id="empty-shell",
                fetched_at="2026-07-06T12:02:00Z",
                raw_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
