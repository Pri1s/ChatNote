from __future__ import annotations

import unittest
from pathlib import Path

from chatnote import queries
from chatnote.store import LedgerStore, RunRecord


FIXTURE_DIR = Path(__file__).parent / "fixtures"
RAW_FIXTURE = FIXTURE_DIR / "ledger_raw_snapshot.json"
TRANSCRIPT_FIXTURE = FIXTURE_DIR / "ledger_transcript.json"


class QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = LedgerStore.open(":memory:")
        ingest = self.store.ingest_capture(
            raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
        )
        self.transcript_id = ingest.transcript_id
        self.conversation_id = ingest.conversation_id
        claims = [
            {
                "transcript_id": self.transcript_id,
                "conversation_id": self.conversation_id,
                "claim_sequence": 0,
                "standalone_claim_text": "The user would probably prefer SQLite for local storage.",
                "speaker_role": "user",
                "speech_act_type": "preference",
                "hedge_level": "medium",
                "source_message_index": 0,
                "source_quote": "I'd probably lean SQLite over Postgres",
                "concept_tags_json": '["storage"]',
            },
            {
                "transcript_id": self.transcript_id,
                "conversation_id": self.conversation_id,
                "claim_sequence": 1,
                "standalone_claim_text": "The claim ledger stays append-only.",
                "speaker_role": "assistant",
                "speech_act_type": "fact",
                "hedge_level": "none",
                "source_message_index": 1,
                "source_block_index": 0,
                "source_char_start": 34,
                "source_char_end": 69,
                "source_quote": "The claim ledger stays append-only.",
                "concept_tags_json": '["ledger"]',
            },
            {
                "transcript_id": self.transcript_id,
                "conversation_id": self.conversation_id,
                "claim_sequence": 2,
                "standalone_claim_text": "The user asked what happens when a share link is captured twice.",
                "speaker_role": "user",
                "speech_act_type": "question",
                "hedge_level": "none",
                "source_message_index": 2,
                "source_quote": "What happens if the same share link is captured twice?",
                "concept_tags_json": "[]",
            },
        ]
        checks = [
            {
                "claim_sequence": 0,
                "support_verdict": "partial",
                "check_method": "s1-011-lexical-v1",
                "quote_found": True,
                "fallback_applied": False,
                "detail": "fixture",
            },
            {
                "claim_sequence": 1,
                "support_verdict": "yes",
                "check_method": "s1-011-lexical-v1",
                "quote_found": True,
                "fallback_applied": False,
                "detail": "fixture",
            },
            {
                "claim_sequence": 2,
                "support_verdict": "no",
                "check_method": "s1-011-lexical-v1",
                "quote_found": False,
                "fallback_applied": True,
                "original_claim_text": "Original rewrite before fallback.",
                "detail": "fixture",
            },
        ]
        self.run_id, self.claim_ids = self.store.record_extraction_result(
            run=RunRecord(
                transcript_id=self.transcript_id,
                extractor_name="fixture-extractor",
                prompt_version="s1-009-v1",
                status="succeeded",
                input_message_count=3,
                output_claim_count=3,
                started_at="2026-07-08T12:00:00Z",
                completed_at="2026-07-08T12:00:02Z",
            ),
            claims=claims,
            support_checks=checks,
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_list_transcripts_by_conversation(self) -> None:
        rows = queries.list_transcripts(self.store, conversation_id=self.conversation_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["transcript_id"], self.transcript_id)

        self.assertEqual(
            queries.list_transcripts(self.store, conversation_id="other-conv"), []
        )

    def test_get_transcript_unknown_id_raises(self) -> None:
        with self.assertRaises(queries.QueryError):
            queries.get_transcript(self.store, transcript_id="missing")

    def test_list_claims_by_conversation_preserves_source_pointers(self) -> None:
        rows = queries.list_claims(self.store, conversation_id=self.conversation_id)
        self.assertEqual(len(rows), 3)
        pointer = rows[1]
        self.assertEqual(pointer["transcript_id"], self.transcript_id)
        self.assertEqual(pointer["source_message_index"], 1)
        self.assertEqual(pointer["source_block_index"], 0)
        self.assertEqual(pointer["source_char_start"], 34)
        self.assertEqual(pointer["source_char_end"], 69)
        self.assertEqual(pointer["source_quote"], "The claim ledger stays append-only.")

    def test_list_claims_by_speaker(self) -> None:
        rows = queries.list_claims(self.store, speaker_role="user")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["speaker_role"] == "user" for row in rows))

    def test_list_claims_by_speech_act(self) -> None:
        rows = queries.list_claims(self.store, speech_act_type="question")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["speech_act_type"], "question")

        combined = queries.list_claims(
            self.store,
            conversation_id=self.conversation_id,
            speaker_role="user",
            speech_act_type="preference",
        )
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["claim_sequence"], 0)

    def test_list_claims_empty_results(self) -> None:
        self.assertEqual(queries.list_claims(self.store, speech_act_type="todo"), [])
        self.assertEqual(
            queries.list_claims(self.store, conversation_id="other-conv"), []
        )

    def test_list_claims_rejects_invalid_filters(self) -> None:
        with self.assertRaisesRegex(queries.QueryError, "speaker role"):
            queries.list_claims(self.store, speaker_role="narrator")
        with self.assertRaisesRegex(queries.QueryError, "speech-act"):
            queries.list_claims(self.store, speech_act_type="belief")

    def test_get_claim_source_traces_back_to_message_and_block(self) -> None:
        traced = queries.get_claim_source(self.store, claim_id=self.claim_ids[1])
        self.assertEqual(traced["claim"]["claim_id"], self.claim_ids[1])
        self.assertEqual(traced["source_message"]["role"], "assistant")
        self.assertIn("append-only", traced["source_message"]["text"])
        self.assertEqual(traced["source_block"]["block_type"], "text")
        self.assertEqual(
            traced["source_block"]["text"][34:69],
            "The claim ledger stays append-only.",
        )

        no_block = queries.get_claim_source(self.store, claim_id=self.claim_ids[0])
        self.assertNotIn("source_block", no_block)
        self.assertEqual(no_block["source_message"]["message_index"], 0)

    def test_get_claim_source_unknown_claim_raises(self) -> None:
        with self.assertRaises(queries.QueryError):
            queries.get_claim_source(self.store, claim_id="missing-claim")

    def test_list_extraction_runs(self) -> None:
        rows = queries.list_extraction_runs(self.store, transcript_id=self.transcript_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], self.run_id)
        self.assertEqual(rows[0]["status"], "succeeded")
        self.assertEqual(
            queries.list_extraction_runs(self.store, transcript_id="missing"), []
        )

    def test_list_support_checks_by_verdict_and_claim(self) -> None:
        fallback_checks = queries.list_support_checks(self.store, support_verdict="no")
        self.assertEqual(len(fallback_checks), 1)
        self.assertEqual(fallback_checks[0]["fallback_applied"], 1)
        self.assertEqual(
            fallback_checks[0]["original_claim_text"],
            "Original rewrite before fallback.",
        )

        claim_checks = queries.list_support_checks(
            self.store, claim_id=self.claim_ids[1]
        )
        self.assertEqual(len(claim_checks), 1)
        self.assertEqual(claim_checks[0]["support_verdict"], "yes")

        with self.assertRaises(queries.QueryError):
            queries.list_support_checks(self.store, support_verdict="maybe")


if __name__ == "__main__":
    unittest.main()
