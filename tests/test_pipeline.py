from __future__ import annotations

import json
import unittest
from pathlib import Path

from chatnote import queries
from chatnote.citation import CitationCheckResult
from chatnote.pipeline import (
    ExtractionPipelineError,
    make_file_extractor,
    run_extraction_pipeline,
)
from chatnote.store import LedgerStore


FIXTURE_DIR = Path(__file__).parent / "fixtures"
RAW_FIXTURE = FIXTURE_DIR / "ledger_raw_snapshot.json"
TRANSCRIPT_FIXTURE = FIXTURE_DIR / "ledger_transcript.json"
VALID_OUTPUT_FIXTURE = FIXTURE_DIR / "extraction_output_valid.json"


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = LedgerStore.open(":memory:")
        ingest = self.store.ingest_capture(
            raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
        )
        self.transcript_id = ingest.transcript_id
        self.conversation_id = ingest.conversation_id

    def tearDown(self) -> None:
        self.store.close()

    def test_fixture_transcript_runs_through_to_ledger_write(self) -> None:
        outcome = run_extraction_pipeline(
            self.store,
            transcript_id=self.transcript_id,
            extractor=make_file_extractor(VALID_OUTPUT_FIXTURE),
            extractor_name="file-extractor",
            model="fixture-model",
        )

        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(len(outcome.claim_ids), 3)
        self.assertEqual(outcome.conversation_id, self.conversation_id)
        self.assertEqual(outcome.fallback_count, 0)

        runs = queries.list_extraction_runs(self.store, transcript_id=self.transcript_id)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["prompt_version"], "s1-009-v1")
        self.assertEqual(len(run["prompt_sha256"]), 64)
        self.assertEqual(run["model"], "fixture-model")
        self.assertEqual(run["input_message_count"], 3)
        self.assertEqual(run["output_claim_count"], 3)
        self.assertIsNotNone(run["completed_at"])

        claims = queries.list_claims(self.store, conversation_id=self.conversation_id)
        self.assertEqual([claim["claim_sequence"] for claim in claims], [0, 1, 2])
        self.assertEqual(claims[0]["source_timestamp"], "2026-07-07T09:59:40Z")
        self.assertIsNone(claims[2]["source_timestamp"])
        self.assertEqual(json.loads(claims[0]["concept_tags_json"]), ["storage", "sqlite"])

        checks = queries.list_support_checks(self.store, run_id=outcome.run_id)
        self.assertEqual(len(checks), 3)
        verdicts = dict(outcome.support_verdicts)
        self.assertEqual(sum(verdicts.values()), 3)
        self.assertEqual(verdicts.get("yes"), 2)
        self.assertEqual(verdicts.get("partial"), 1)

    def test_malformed_json_records_failed_run_and_raises(self) -> None:
        with self.assertRaises(ExtractionPipelineError) as ctx:
            run_extraction_pipeline(
                self.store,
                transcript_id=self.transcript_id,
                extractor=lambda prompt: "this is not json",
                extractor_name="broken-extractor",
            )
        self.assertIsNotNone(ctx.exception.run_id)

        runs = queries.list_extraction_runs(self.store, transcript_id=self.transcript_id)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("not valid JSON", runs[0]["error_message"])
        self.assertEqual(runs[0]["output_claim_count"], 0)
        self.assertEqual(queries.list_claims(self.store), [])

    def test_contract_violation_records_failed_run(self) -> None:
        bad_output = {
            "claims": [
                {
                    "standalone_claim_text": "A claim about nothing.",
                    "speaker_role": "assistant",
                    "speech_act_type": "belief",
                    "hedge_level": "none",
                    "source_message_index": 99,
                    "source_quote": "missing",
                }
            ]
        }
        with self.assertRaises(ExtractionPipelineError):
            run_extraction_pipeline(
                self.store,
                transcript_id=self.transcript_id,
                extractor=lambda prompt: bad_output,
                extractor_name="broken-extractor",
            )
        runs = queries.list_extraction_runs(self.store, transcript_id=self.transcript_id)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("speech_act_type", runs[0]["error_message"])
        self.assertIn("source_message_index 99", runs[0]["error_message"])

    def test_extractor_exception_records_failed_run(self) -> None:
        def exploding_extractor(prompt: str) -> str:
            raise RuntimeError("model unavailable")

        with self.assertRaises(ExtractionPipelineError):
            run_extraction_pipeline(
                self.store,
                transcript_id=self.transcript_id,
                extractor=exploding_extractor,
                extractor_name="broken-extractor",
            )
        runs = queries.list_extraction_runs(self.store, transcript_id=self.transcript_id)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("model unavailable", runs[0]["error_message"])

    def test_unknown_transcript_raises_before_any_write(self) -> None:
        with self.assertRaises(queries.QueryError):
            run_extraction_pipeline(
                self.store,
                transcript_id="missing-transcript",
                extractor=lambda prompt: {"claims": []},
                extractor_name="file-extractor",
            )
        self.assertEqual(queries.list_extraction_runs(self.store), [])

    def test_unsupported_claim_is_stored_as_quote_fallback(self) -> None:
        output = {
            "claims": [
                {
                    "standalone_claim_text": "Postgres replication needs three failover nodes.",
                    "speaker_role": "user",
                    "speech_act_type": "fact",
                    "hedge_level": "none",
                    "source_message_index": 2,
                    "source_quote": "a quote that is not in the message",
                }
            ]
        }
        outcome = run_extraction_pipeline(
            self.store,
            transcript_id=self.transcript_id,
            extractor=lambda prompt: output,
            extractor_name="file-extractor",
        )
        self.assertEqual(outcome.fallback_count, 1)
        self.assertEqual(dict(outcome.support_verdicts), {"no": 1})

        claims = queries.list_claims(self.store, conversation_id=self.conversation_id)
        source_text = "What happens if the same share link is captured twice?"
        self.assertEqual(claims[0]["standalone_claim_text"], source_text)
        self.assertEqual(claims[0]["source_quote"], source_text)

        checks = queries.list_support_checks(self.store, run_id=outcome.run_id)
        self.assertEqual(checks[0]["support_verdict"], "no")
        self.assertEqual(checks[0]["fallback_applied"], 1)
        self.assertEqual(
            checks[0]["original_claim_text"],
            "Postgres replication needs three failover nodes.",
        )

    def test_injected_checker_controls_support_results(self) -> None:
        def always_unknown(claim, source_text):
            return CitationCheckResult(
                verdict="unknown",
                quote_found=True,
                detail="mocked checker",
                method="mock-checker",
            )

        outcome = run_extraction_pipeline(
            self.store,
            transcript_id=self.transcript_id,
            extractor=make_file_extractor(VALID_OUTPUT_FIXTURE),
            extractor_name="file-extractor",
            checker=always_unknown,
        )
        self.assertEqual(dict(outcome.support_verdicts), {"unknown": 3})
        self.assertEqual(outcome.fallback_count, 3)
        checks = queries.list_support_checks(self.store, run_id=outcome.run_id)
        self.assertTrue(all(check["check_method"] == "mock-checker" for check in checks))
        claims = queries.list_claims(self.store, conversation_id=self.conversation_id)
        for claim in claims:
            self.assertEqual(claim["standalone_claim_text"], claim["source_quote"])


if __name__ == "__main__":
    unittest.main()
