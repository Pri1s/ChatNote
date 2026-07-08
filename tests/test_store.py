from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from chatnote.store import (
    DuplicateArtifactError,
    LedgerStore,
    RunRecord,
    StoreError,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
RAW_FIXTURE = FIXTURE_DIR / "ledger_raw_snapshot.json"
TRANSCRIPT_FIXTURE = FIXTURE_DIR / "ledger_transcript.json"


def make_run(transcript_id: str, *, status: str = "succeeded", claim_count: int = 0) -> RunRecord:
    return RunRecord(
        transcript_id=transcript_id,
        extractor_name="fixture-extractor",
        prompt_version="s1-009-v1",
        status=status,
        input_message_count=3,
        output_claim_count=claim_count,
        started_at="2026-07-08T12:00:00Z",
        completed_at="2026-07-08T12:00:01Z",
    )


def make_claim_row(
    transcript_id: str, *, claim_sequence: int = 0, **overrides
) -> dict:
    row = {
        "transcript_id": transcript_id,
        "conversation_id": "ledger-fixture-conv",
        "claim_sequence": claim_sequence,
        "standalone_claim_text": "The claim ledger stays append-only.",
        "speaker_role": "assistant",
        "speech_act_type": "fact",
        "hedge_level": "none",
        "source_message_index": 1,
        "source_quote": "The claim ledger stays append-only.",
        "concept_tags_json": '["ledger"]',
    }
    row.update(overrides)
    return row


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = LedgerStore.open(":memory:")

    def tearDown(self) -> None:
        self.store.close()

    def test_open_applies_schema_including_support_checks(self) -> None:
        tables = {
            row[0]
            for row in self.store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {
                "raw_artifacts",
                "transcripts",
                "transcript_messages",
                "transcript_message_blocks",
                "transcript_warnings",
                "extraction_runs",
                "claim_ledger",
                "claim_support_checks",
            }.issubset(tables)
        )

    def test_open_on_disk_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nested" / "chatnote.db"
            first = LedgerStore.open(db_path)
            first.close()
            second = LedgerStore.open(db_path)
            result = second.ingest_capture(
                raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
            )
            second.close()
            self.assertEqual(result.conversation_id, "ledger-fixture-conv")
            self.assertTrue(db_path.is_file())

    def test_ingest_capture_stores_transcript_records(self) -> None:
        result = self.store.ingest_capture(
            raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
        )

        self.assertEqual(result.conversation_id, "ledger-fixture-conv")
        self.assertEqual(result.message_count, 3)
        self.assertEqual(result.warning_count, 1)

        conn = self.store.connection
        transcript = conn.execute(
            "SELECT * FROM transcripts WHERE transcript_id = ?",
            (result.transcript_id,),
        ).fetchone()
        self.assertEqual(transcript["title"], "Ledger fixture conversation")
        self.assertEqual(transcript["parser_method"], "json_structured_data")
        self.assertEqual(transcript["message_count"], 3)

        artifacts = conn.execute(
            "SELECT artifact_kind, byte_size, sha256 FROM raw_artifacts ORDER BY artifact_kind"
        ).fetchall()
        self.assertEqual(
            [row["artifact_kind"] for row in artifacts],
            ["parsed_transcript", "source_snapshot"],
        )
        for row in artifacts:
            self.assertGreater(row["byte_size"], 0)
            self.assertEqual(len(row["sha256"]), 64)

        messages = conn.execute(
            "SELECT message_index, role, timestamp FROM transcript_messages "
            "WHERE transcript_id = ? ORDER BY message_index",
            (result.transcript_id,),
        ).fetchall()
        self.assertEqual([row["message_index"] for row in messages], [0, 1, 2])
        self.assertIsNone(messages[2]["timestamp"])

        blocks = conn.execute(
            "SELECT block_type, language FROM transcript_message_blocks "
            "WHERE transcript_id = ? AND message_index = 1 ORDER BY block_index",
            (result.transcript_id,),
        ).fetchall()
        self.assertEqual([row["block_type"] for row in blocks], ["text", "code"])
        self.assertEqual(blocks[1]["language"], "python")

        warnings = conn.execute(
            "SELECT code, message_index FROM transcript_warnings WHERE transcript_id = ?",
            (result.transcript_id,),
        ).fetchall()
        self.assertEqual(warnings[0]["code"], "missing_timestamp")
        self.assertEqual(warnings[0]["message_index"], 2)

    def test_ingest_capture_rejects_duplicate_artifact_path(self) -> None:
        self.store.ingest_capture(
            raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
        )
        with self.assertRaises(DuplicateArtifactError):
            self.store.ingest_capture(
                raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
            )

    def test_ingest_capture_rejects_transcript_without_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_transcript = Path(tmpdir) / "bad.json"
            bad_transcript.write_text(json.dumps({"messages": []}), encoding="utf-8")
            with self.assertRaisesRegex(StoreError, "metadata"):
                self.store.ingest_capture(
                    raw_path=RAW_FIXTURE, transcript_path=bad_transcript
                )

    def test_stored_records_are_immutable(self) -> None:
        result = self.store.ingest_capture(
            raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
        )
        run_id, claim_ids = self.store.record_extraction_result(
            run=make_run(result.transcript_id, claim_count=1),
            claims=[make_claim_row(result.transcript_id)],
            support_checks=[
                {
                    "claim_sequence": 0,
                    "support_verdict": "yes",
                    "check_method": "s1-011-lexical-v1",
                    "quote_found": True,
                    "fallback_applied": False,
                    "detail": "fixture",
                }
            ],
        )
        self.assertEqual(len(claim_ids), 1)

        mutations = [
            "UPDATE raw_artifacts SET media_type = 'text/plain'",
            "DELETE FROM raw_artifacts",
            "UPDATE transcripts SET title = 'changed'",
            "DELETE FROM transcript_messages",
            "UPDATE extraction_runs SET status = 'failed'",
            "DELETE FROM claim_ledger",
            "UPDATE claim_support_checks SET support_verdict = 'no'",
            "DELETE FROM claim_support_checks",
        ]
        for statement in mutations:
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.store.connection.execute(statement)

    def test_append_only_corrections_add_rows_without_touching_history(self) -> None:
        result = self.store.ingest_capture(
            raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
        )
        _, first_claim_ids = self.store.record_extraction_result(
            run=make_run(result.transcript_id, claim_count=1),
            claims=[make_claim_row(result.transcript_id)],
        )
        _, second_claim_ids = self.store.record_extraction_result(
            run=make_run(result.transcript_id, claim_count=1),
            claims=[
                make_claim_row(
                    result.transcript_id,
                    standalone_claim_text="The claim ledger is append-only by design.",
                    supersedes_claim_id=first_claim_ids[0],
                )
            ],
        )

        rows = self.store.connection.execute(
            "SELECT claim_id, supersedes_claim_id FROM claim_ledger ORDER BY created_at, claim_id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        by_id = {row["claim_id"]: row["supersedes_claim_id"] for row in rows}
        self.assertIsNone(by_id[first_claim_ids[0]])
        self.assertEqual(by_id[second_claim_ids[0]], first_claim_ids[0])

    def test_record_extraction_result_rejects_invalid_rows(self) -> None:
        result = self.store.ingest_capture(
            raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
        )
        with self.assertRaises(StoreError):
            self.store.record_extraction_result(
                run=make_run(result.transcript_id, claim_count=1),
                claims=[
                    make_claim_row(result.transcript_id, speech_act_type="belief")
                ],
            )
        with self.assertRaises(StoreError):
            self.store.record_extraction_result(
                run=make_run(result.transcript_id, status="exploded")
            )
        with self.assertRaises(StoreError):
            self.store.record_extraction_result(
                run=make_run(result.transcript_id),
                support_checks=[
                    {
                        "claim_sequence": 5,
                        "support_verdict": "yes",
                        "check_method": "s1-011-lexical-v1",
                        "quote_found": True,
                        "fallback_applied": False,
                    }
                ],
            )

    def test_failed_runs_are_recorded_without_claims(self) -> None:
        result = self.store.ingest_capture(
            raw_path=RAW_FIXTURE, transcript_path=TRANSCRIPT_FIXTURE
        )
        run = RunRecord(
            transcript_id=result.transcript_id,
            extractor_name="fixture-extractor",
            prompt_version="s1-009-v1",
            status="failed",
            error_message="Output is not valid JSON.",
            input_message_count=3,
            output_claim_count=0,
            started_at="2026-07-08T12:00:00Z",
            completed_at="2026-07-08T12:00:01Z",
        )
        run_id, claim_ids = self.store.record_extraction_result(run=run)
        self.assertEqual(claim_ids, [])
        row = self.store.connection.execute(
            "SELECT status, error_message FROM extraction_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("not valid JSON", row["error_message"])


if __name__ == "__main__":
    unittest.main()
