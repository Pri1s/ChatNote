from __future__ import annotations

import json
import unittest
from pathlib import Path

from chatnote.extraction_contract import (
    PROMPT_VERSION,
    ExtractionValidationError,
    build_extraction_prompt,
    parse_extraction_output,
    prompt_sha256,
    validate_extraction_output,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
TRANSCRIPT = json.loads(
    (FIXTURE_DIR / "ledger_transcript.json").read_text(encoding="utf-8")
)
VALID_OUTPUT = json.loads(
    (FIXTURE_DIR / "extraction_output_valid.json").read_text(encoding="utf-8")
)


def valid_claim(**overrides) -> dict:
    claim = json.loads(json.dumps(VALID_OUTPUT["claims"][0]))
    claim.update(overrides)
    return claim


class PromptTests(unittest.TestCase):
    def test_prompt_is_deterministic_and_versioned(self) -> None:
        first = build_extraction_prompt(TRANSCRIPT)
        second = build_extraction_prompt(TRANSCRIPT)
        self.assertEqual(first, second)
        self.assertEqual(prompt_sha256(first), prompt_sha256(second))
        self.assertEqual(len(prompt_sha256(first)), 64)
        self.assertEqual(PROMPT_VERSION, "s1-009-v1")

    def test_prompt_contains_transcript_and_contract_rules(self) -> None:
        prompt = build_extraction_prompt(TRANSCRIPT)
        self.assertIn("conversation_id: ledger-fixture-conv", prompt)
        self.assertIn("title: Ledger fixture conversation", prompt)
        self.assertIn("[0] user (2026-07-07T09:59:40Z):", prompt)
        self.assertIn("[2] user (no timestamp):", prompt)
        self.assertIn("I'd probably lean SQLite over Postgres", prompt)
        self.assertIn('"claims"', prompt)
        self.assertIn("standalone_claim_text", prompt)
        self.assertIn("outside knowledge", prompt)


class ParseTests(unittest.TestCase):
    def test_parse_accepts_json_text_and_dicts(self) -> None:
        payload = parse_extraction_output(json.dumps(VALID_OUTPUT))
        self.assertEqual(payload, VALID_OUTPUT)
        self.assertEqual(parse_extraction_output(VALID_OUTPUT), VALID_OUTPUT)

    def test_parse_rejects_invalid_json_and_non_objects(self) -> None:
        with self.assertRaises(ExtractionValidationError) as ctx:
            parse_extraction_output("not json at all")
        self.assertIn("not valid JSON", ctx.exception.errors[0])

        with self.assertRaises(ExtractionValidationError):
            parse_extraction_output("[1, 2, 3]")


class ValidationTests(unittest.TestCase):
    def test_valid_fixture_output_passes(self) -> None:
        claims = validate_extraction_output(VALID_OUTPUT, TRANSCRIPT)
        self.assertEqual(len(claims), 3)
        self.assertEqual(claims[0].speech_act_type, "preference")
        self.assertEqual(claims[0].hedge_level, "medium")
        self.assertEqual(claims[0].concept_tags, ("storage", "sqlite"))
        self.assertEqual(claims[1].source_block_index, 0)
        self.assertEqual(claims[1].source_char_start, 34)
        self.assertEqual(claims[2].concept_tags, ())

    def test_empty_claims_list_is_valid(self) -> None:
        self.assertEqual(validate_extraction_output({"claims": []}, TRANSCRIPT), [])

    def test_missing_claims_key_raises(self) -> None:
        with self.assertRaises(ExtractionValidationError) as ctx:
            validate_extraction_output({}, TRANSCRIPT)
        self.assertIn('"claims"', ctx.exception.errors[0])

    def test_unknown_fields_are_rejected(self) -> None:
        with self.assertRaises(ExtractionValidationError) as ctx:
            validate_extraction_output(
                {"claims": [valid_claim(confidence=0.9)], "notes": "extra"},
                TRANSCRIPT,
            )
        errors = "\n".join(ctx.exception.errors)
        self.assertIn("Unexpected top-level keys", errors)
        self.assertIn("unexpected fields ['confidence']", errors)

    def test_errors_are_aggregated_across_claims(self) -> None:
        bad_output = {
            "claims": [
                valid_claim(speech_act_type="belief", hedge_level="certain"),
                valid_claim(standalone_claim_text="   "),
                {"speaker_role": "user"},
            ]
        }
        with self.assertRaises(ExtractionValidationError) as ctx:
            validate_extraction_output(bad_output, TRANSCRIPT)
        errors = "\n".join(ctx.exception.errors)
        self.assertIn("claims[0]: invalid speech_act_type 'belief'", errors)
        self.assertIn("claims[0]: invalid hedge_level 'certain'", errors)
        self.assertIn("claims[1]: standalone_claim_text", errors)
        self.assertIn("claims[2]: missing required fields", errors)

    def test_source_pointer_must_exist(self) -> None:
        with self.assertRaises(ExtractionValidationError) as ctx:
            validate_extraction_output(
                {"claims": [valid_claim(source_message_index=99)]}, TRANSCRIPT
            )
        self.assertIn("source_message_index 99", ctx.exception.errors[0])

        with self.assertRaises(ExtractionValidationError) as ctx:
            validate_extraction_output(
                {"claims": [valid_claim(source_block_index=5)]}, TRANSCRIPT
            )
        self.assertIn("source_block_index 5", ctx.exception.errors[0])

    def test_speaker_role_must_match_source_message_role(self) -> None:
        with self.assertRaises(ExtractionValidationError) as ctx:
            validate_extraction_output(
                {"claims": [valid_claim(speaker_role="assistant")]}, TRANSCRIPT
            )
        self.assertIn("does not match source message role", ctx.exception.errors[0])

    def test_char_offsets_must_reproduce_quote(self) -> None:
        with self.assertRaises(ExtractionValidationError) as ctx:
            validate_extraction_output(
                {
                    "claims": [
                        valid_claim(source_char_start=0, source_char_end=10)
                    ]
                },
                TRANSCRIPT,
            )
        self.assertIn("do not reproduce source_quote", ctx.exception.errors[0])

        with self.assertRaises(ExtractionValidationError) as ctx:
            validate_extraction_output(
                {
                    "claims": [
                        valid_claim(source_char_start=0, source_char_end=100000)
                    ]
                },
                TRANSCRIPT,
            )
        self.assertIn("out of", ctx.exception.errors[0])

        with self.assertRaises(ExtractionValidationError) as ctx:
            validate_extraction_output(
                {"claims": [valid_claim(source_char_start=0)]}, TRANSCRIPT
            )
        self.assertIn("provided together", ctx.exception.errors[0])

    def test_explicit_null_optional_fields_are_treated_as_absent(self) -> None:
        claims = validate_extraction_output(
            {
                "claims": [
                    valid_claim(
                        source_block_index=None,
                        source_char_start=None,
                        source_char_end=None,
                        speaker_label=None,
                        concept_tags=None,
                    )
                ]
            },
            TRANSCRIPT,
        )
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertIsNone(claim.source_block_index)
        self.assertIsNone(claim.source_char_start)
        self.assertIsNone(claim.source_char_end)
        self.assertIsNone(claim.speaker_label)
        self.assertEqual(claim.concept_tags, ())
        row = claim.to_ledger_row(
            transcript_id="tr-1",
            conversation_id="ledger-fixture-conv",
            claim_sequence=0,
        )
        self.assertIsNone(row["source_block_index"])
        self.assertEqual(json.loads(row["concept_tags_json"]), [])

    def test_concept_tags_must_be_non_empty_strings(self) -> None:
        with self.assertRaises(ExtractionValidationError):
            validate_extraction_output(
                {"claims": [valid_claim(concept_tags=["ok", ""])]}, TRANSCRIPT
            )
        with self.assertRaises(ExtractionValidationError):
            validate_extraction_output(
                {"claims": [valid_claim(concept_tags="storage")]}, TRANSCRIPT
            )


class LedgerMappingTests(unittest.TestCase):
    def test_to_ledger_row_maps_contract_fields(self) -> None:
        claims = validate_extraction_output(VALID_OUTPUT, TRANSCRIPT)
        row = claims[1].to_ledger_row(
            transcript_id="tr-1",
            conversation_id="ledger-fixture-conv",
            claim_sequence=1,
            source_timestamp="2026-07-07T09:59:52Z",
        )
        self.assertEqual(row["transcript_id"], "tr-1")
        self.assertEqual(row["claim_sequence"], 1)
        self.assertEqual(row["standalone_claim_text"], "The claim ledger stays append-only.")
        self.assertEqual(row["speaker_role"], "assistant")
        self.assertEqual(row["speech_act_type"], "fact")
        self.assertEqual(row["hedge_level"], "none")
        self.assertEqual(row["source_message_index"], 1)
        self.assertEqual(row["source_block_index"], 0)
        self.assertEqual(row["source_char_start"], 34)
        self.assertEqual(row["source_char_end"], 69)
        self.assertEqual(row["source_timestamp"], "2026-07-07T09:59:52Z")
        self.assertEqual(json.loads(row["concept_tags_json"]), ["ledger"])
        self.assertIsNone(row["speaker_label"])


if __name__ == "__main__":
    unittest.main()
