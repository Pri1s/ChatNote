from __future__ import annotations

import json
import unittest
from pathlib import Path

from chatnote import queries
from chatnote.llm_extractor import (
    DEFAULT_BASE_URL,
    LLMExtractorError,
    OpenRouterConfig,
    build_openrouter_extractor,
    load_openrouter_config,
)
from chatnote.pipeline import ExtractionPipelineError, run_extraction_pipeline
from chatnote.store import LedgerStore


FIXTURE_DIR = Path(__file__).parent / "fixtures"
RAW_FIXTURE = FIXTURE_DIR / "ledger_raw_snapshot.json"
TRANSCRIPT_FIXTURE = FIXTURE_DIR / "ledger_transcript.json"
VALID_OUTPUT_FIXTURE = FIXTURE_DIR / "extraction_output_valid.json"

CONFIG = OpenRouterConfig(model="test/model", api_key="test-key")


def completion_response(content: str, *, finish_reason: str = "stop") -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ]
    }


class ConfigTests(unittest.TestCase):
    def test_model_flag_wins_over_environment(self) -> None:
        config = load_openrouter_config(
            model="flag/model",
            env={"CHATNOTE_MODEL": "env/model", "OPENROUTER_API_KEY": "key"},
        )
        self.assertEqual(config.model, "flag/model")

    def test_model_falls_back_to_environment(self) -> None:
        config = load_openrouter_config(
            env={"CHATNOTE_MODEL": "env/model", "OPENROUTER_API_KEY": "key"}
        )
        self.assertEqual(config.model, "env/model")
        self.assertEqual(config.api_key, "key")
        self.assertEqual(config.base_url, DEFAULT_BASE_URL)

    def test_missing_model_is_a_config_error(self) -> None:
        with self.assertRaises(LLMExtractorError) as ctx:
            load_openrouter_config(env={"OPENROUTER_API_KEY": "key"})
        self.assertIn("CHATNOTE_MODEL", str(ctx.exception))

    def test_missing_api_key_is_a_config_error(self) -> None:
        with self.assertRaises(LLMExtractorError) as ctx:
            load_openrouter_config(env={"CHATNOTE_MODEL": "env/model"})
        self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))

    def test_base_url_override_is_normalized(self) -> None:
        config = load_openrouter_config(
            env={
                "CHATNOTE_MODEL": "env/model",
                "OPENROUTER_API_KEY": "key",
                "CHATNOTE_LLM_BASE_URL": "http://localhost:11434/v1/",
            }
        )
        self.assertEqual(config.base_url, "http://localhost:11434/v1")


class ExtractorTests(unittest.TestCase):
    def test_request_shape_matches_openai_chat_completions(self) -> None:
        seen: list[dict] = []

        def transport(request: dict) -> dict:
            seen.append(request)
            return completion_response('{"claims": []}')

        extractor = build_openrouter_extractor(CONFIG, transport=transport)
        self.assertEqual(extractor("PROMPT TEXT"), '{"claims": []}')

        self.assertEqual(len(seen), 1)
        request = seen[0]
        self.assertEqual(request["url"], f"{DEFAULT_BASE_URL}/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertEqual(request["body"]["model"], "test/model")
        self.assertEqual(
            request["body"]["messages"],
            [{"role": "user", "content": "PROMPT TEXT"}],
        )
        self.assertEqual(request["body"]["max_tokens"], CONFIG.max_output_tokens)
        self.assertEqual(request["timeout_seconds"], CONFIG.timeout_seconds)

    def test_markdown_code_fences_are_stripped(self) -> None:
        fenced = '```json\n{"claims": []}\n```'
        extractor = build_openrouter_extractor(
            CONFIG, transport=lambda request: completion_response(fenced)
        )
        self.assertEqual(extractor("prompt"), '{"claims": []}')

    def test_api_error_payload_raises(self) -> None:
        extractor = build_openrouter_extractor(
            CONFIG,
            transport=lambda request: {"error": {"message": "No such model"}},
        )
        with self.assertRaises(LLMExtractorError) as ctx:
            extractor("prompt")
        self.assertIn("No such model", str(ctx.exception))

    def test_empty_choices_raises(self) -> None:
        extractor = build_openrouter_extractor(
            CONFIG, transport=lambda request: {"choices": []}
        )
        with self.assertRaises(LLMExtractorError) as ctx:
            extractor("prompt")
        self.assertIn("no choices", str(ctx.exception))

    def test_truncated_output_raises(self) -> None:
        extractor = build_openrouter_extractor(
            CONFIG,
            transport=lambda request: completion_response(
                '{"claims": [', finish_reason="length"
            ),
        )
        with self.assertRaises(LLMExtractorError) as ctx:
            extractor("prompt")
        self.assertIn("truncated", str(ctx.exception))

    def test_missing_message_content_raises(self) -> None:
        extractor = build_openrouter_extractor(
            CONFIG,
            transport=lambda request: {"choices": [{"message": {"content": "  "}}]},
        )
        with self.assertRaises(LLMExtractorError):
            extractor("prompt")


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = LedgerStore.open(":memory:")
        self.addCleanup(self.store.close)
        ingest = self.store.ingest_capture(
            raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
        )
        self.transcript_id = ingest.transcript_id

    def test_stubbed_model_output_writes_real_ledger_rows(self) -> None:
        content = VALID_OUTPUT_FIXTURE.read_text(encoding="utf-8")
        extractor = build_openrouter_extractor(
            CONFIG,
            transport=lambda request: completion_response(f"```json\n{content}\n```"),
        )

        outcome = run_extraction_pipeline(
            self.store,
            transcript_id=self.transcript_id,
            extractor=extractor,
            extractor_name="openrouter",
            model=CONFIG.model,
        )

        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(len(outcome.claim_ids), 3)
        runs = queries.list_extraction_runs(self.store, transcript_id=self.transcript_id)
        self.assertEqual(runs[0]["extractor_name"], "openrouter")
        self.assertEqual(runs[0]["model"], "test/model")
        self.assertEqual(
            len(queries.list_claims(self.store)), 3
        )

    def test_failing_transport_records_failed_run(self) -> None:
        def transport(request: dict) -> dict:
            raise LLMExtractorError("LLM API request failed: HTTP 502")

        extractor = build_openrouter_extractor(CONFIG, transport=transport)
        with self.assertRaises(ExtractionPipelineError):
            run_extraction_pipeline(
                self.store,
                transcript_id=self.transcript_id,
                extractor=extractor,
                extractor_name="openrouter",
                model=CONFIG.model,
            )

        runs = queries.list_extraction_runs(self.store, transcript_id=self.transcript_id)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("HTTP 502", runs[0]["error_message"])
        self.assertEqual(queries.list_claims(self.store), [])


if __name__ == "__main__":
    unittest.main()
