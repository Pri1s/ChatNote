from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "s1-005-schema-v1.sql"
EXTRACTION_OUTPUT_SCHEMA_PATH = ROOT / "docs" / "s1-012-raw-extraction-output-v1.sql"


class SchemaContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.executescript(EXTRACTION_OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.conn.close()

    def test_schema_creates_required_tables_indexes_and_triggers(self) -> None:
        tables = self._names("table")
        self.assertTrue(
            {
                "raw_artifacts",
                "transcripts",
                "transcript_messages",
                "transcript_message_blocks",
                "transcript_warnings",
                "extraction_runs",
                "claim_ledger",
                "extraction_outputs",
            }.issubset(tables)
        )

        indexes = self._names("index")
        self.assertTrue(
            {
                "idx_claim_ledger_conversation",
                "idx_claim_ledger_speaker_role",
                "idx_claim_ledger_speech_act_type",
                "idx_claim_ledger_source_pointer",
            }.issubset(indexes)
        )

        triggers = self._names("trigger")
        self.assertTrue(
            {
                "raw_artifacts_no_update",
                "raw_artifacts_no_delete",
                "transcripts_no_update",
                "transcripts_no_delete",
                "claim_ledger_no_update",
                "claim_ledger_no_delete",
            }.issubset(triggers)
        )

    def test_schema_accepts_sample_claim_and_rejects_mutation(self) -> None:
        self._insert_sample_claim()
        self.conn.execute(
            """
            INSERT INTO extraction_outputs (run_id, file_path, sha256, byte_size)
            VALUES (?, ?, ?, ?)
            """,
            ("run-1", "data/extractions/run-1.json", "d" * 64, 42),
        )

        immutable_statements = [
            ("UPDATE raw_artifacts SET media_type = 'text/plain'", "raw_artifacts is immutable"),
            ("DELETE FROM raw_artifacts", "raw_artifacts is immutable"),
            ("UPDATE transcripts SET title = 'Changed'", "transcripts is immutable"),
            ("DELETE FROM transcripts", "transcripts is immutable"),
            ("UPDATE extraction_runs SET status = 'failed'", "extraction_runs is append-only"),
            ("DELETE FROM extraction_runs", "extraction_runs is append-only"),
            ("DELETE FROM extraction_outputs", "extraction_outputs is append-only"),
            (
                "UPDATE claim_ledger SET hedge_level = 'high'",
                "claim_ledger is append-only",
            ),
            ("DELETE FROM claim_ledger", "claim_ledger is append-only"),
        ]
        for statement, message in immutable_statements:
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(sqlite3.IntegrityError, message):
                    self.conn.execute(statement)

    def test_claim_constraints_reject_invalid_labels_and_concept_tags(self) -> None:
        self._insert_sample_transcript()
        self.conn.execute(
            """
            INSERT INTO extraction_runs (
                run_id,
                transcript_id,
                extractor_name,
                prompt_version,
                status,
                input_message_count,
                output_claim_count,
                started_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "transcript-1",
                "fixture-extractor",
                "s1-009-v1",
                "succeeded",
                1,
                0,
                "2026-07-08T12:00:00Z",
            ),
        )

        invalid_rows = [
            {"speech_act_type": "belief", "hedge_level": "none", "concept_tags_json": "[]"},
            {"speech_act_type": "fact", "hedge_level": "certain", "concept_tags_json": "[]"},
            {
                "speech_act_type": "fact",
                "hedge_level": "none",
                "concept_tags_json": '{"tag":"not-array"}',
            },
        ]
        for sequence, row in enumerate(invalid_rows):
            with self.subTest(row=row):
                with self.assertRaises(sqlite3.IntegrityError):
                    self._insert_claim(
                        claim_id=f"bad-claim-{sequence}",
                        claim_sequence=sequence,
                        speech_act_type=row["speech_act_type"],
                        hedge_level=row["hedge_level"],
                        concept_tags_json=row["concept_tags_json"],
                    )

    def _names(self, kind: str) -> set[str]:
        return {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = ?",
                (kind,),
            )
        }

    def _insert_sample_claim(self) -> None:
        self._insert_sample_transcript()
        self.conn.execute(
            """
            INSERT INTO extraction_runs (
                run_id,
                transcript_id,
                extractor_name,
                prompt_version,
                prompt_sha256,
                model,
                status,
                input_message_count,
                output_claim_count,
                started_at,
                completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "transcript-1",
                "fixture-extractor",
                "s1-009-v1",
                "c" * 64,
                "fixture-model",
                "succeeded",
                1,
                1,
                "2026-07-08T12:00:00Z",
                "2026-07-08T12:00:01Z",
            ),
        )
        self._insert_claim(
            claim_id="claim-1",
            claim_sequence=0,
            speech_act_type="fact",
            hedge_level="none",
            concept_tags_json='["schema"]',
        )

    def _insert_sample_transcript(self) -> None:
        artifact_rows = [
            (
                "source-artifact-1",
                "source_snapshot",
                "data/raw/fixture.json",
                "application/json",
                "a" * 64,
            ),
            (
                "transcript-artifact-1",
                "parsed_transcript",
                "data/transcripts/fixture.json",
                "application/json",
                "b" * 64,
            ),
        ]
        for artifact_id, artifact_kind, file_path, media_type, sha256 in artifact_rows:
            self.conn.execute(
                """
                INSERT INTO raw_artifacts (
                    artifact_id,
                    conversation_id,
                    source_url,
                    artifact_kind,
                    file_path,
                    media_type,
                    sha256,
                    byte_size,
                    captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    "conversation-1",
                    "https://claude.ai/share/conversation-1",
                    artifact_kind,
                    file_path,
                    media_type,
                    sha256,
                    123,
                    "2026-07-08T12:00:00Z",
                ),
            )

        self.conn.execute(
            """
            INSERT INTO transcripts (
                transcript_id,
                conversation_id,
                title,
                source_artifact_id,
                transcript_artifact_id,
                parser_method,
                fetched_at,
                message_count,
                warning_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "transcript-1",
                "conversation-1",
                "Fixture",
                "source-artifact-1",
                "transcript-artifact-1",
                "json_structured_data",
                "2026-07-08T12:00:00Z",
                1,
                0,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO transcript_messages (
                transcript_id,
                conversation_id,
                message_index,
                role,
                text,
                timestamp,
                provenance_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "transcript-1",
                "conversation-1",
                0,
                "assistant",
                "The schema is append-only.",
                "2026-07-08T12:00:00Z",
                '{"source":"fixture"}',
            ),
        )
        self.conn.execute(
            """
            INSERT INTO transcript_message_blocks (
                transcript_id,
                message_index,
                block_index,
                block_type,
                text,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "transcript-1",
                0,
                0,
                "text",
                "The schema is append-only.",
                "{}",
            ),
        )

    def _insert_claim(
        self,
        *,
        claim_id: str,
        claim_sequence: int,
        speech_act_type: str,
        hedge_level: str,
        concept_tags_json: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO claim_ledger (
                claim_id,
                run_id,
                transcript_id,
                conversation_id,
                claim_sequence,
                standalone_claim_text,
                speaker_role,
                speech_act_type,
                hedge_level,
                source_message_index,
                source_block_index,
                source_char_start,
                source_char_end,
                source_quote,
                source_timestamp,
                concept_tags_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_id,
                "run-1",
                "transcript-1",
                "conversation-1",
                claim_sequence,
                "The schema is append-only.",
                "assistant",
                speech_act_type,
                hedge_level,
                0,
                0,
                0,
                26,
                "The schema is append-only.",
                "2026-07-08T12:00:00Z",
                concept_tags_json,
            ),
        )


if __name__ == "__main__":
    unittest.main()
