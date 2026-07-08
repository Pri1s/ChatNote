from __future__ import annotations

import unittest

from chatnote.citation import (
    CitationCheckResult,
    check_citation_support,
    resolve_claim_support,
    to_support_check_row,
)
from chatnote.extraction_contract import ExtractedClaim


SOURCE_TEXT = (
    "SQLite fits a local-first design. The claim ledger stays append-only, "
    "and raw transcripts are never overwritten."
)


def make_claim(**overrides) -> ExtractedClaim:
    fields = {
        "standalone_claim_text": "The claim ledger stays append-only.",
        "speaker_role": "assistant",
        "speech_act_type": "fact",
        "hedge_level": "none",
        "source_message_index": 1,
        "source_quote": "The claim ledger stays append-only",
    }
    fields.update(overrides)
    return ExtractedClaim(**fields)


class CitationCheckTests(unittest.TestCase):
    def test_supported_claim_is_yes(self) -> None:
        result = check_citation_support(make_claim(), SOURCE_TEXT)
        self.assertEqual(result.verdict, "yes")
        self.assertTrue(result.quote_found)

    def test_partially_supported_claim_is_partial(self) -> None:
        claim = make_claim(
            standalone_claim_text=(
                "The claim ledger stays append-only and corrections spawn new entries."
            )
        )
        result = check_citation_support(claim, SOURCE_TEXT)
        self.assertEqual(result.verdict, "partial")
        self.assertTrue(result.quote_found)

    def test_unsupported_rewrite_is_no_even_when_quote_matches(self) -> None:
        claim = make_claim(
            standalone_claim_text="Postgres replication requires three dedicated failover nodes."
        )
        result = check_citation_support(claim, SOURCE_TEXT)
        self.assertEqual(result.verdict, "no")
        self.assertTrue(result.quote_found)

    def test_quote_not_in_source_is_no(self) -> None:
        claim = make_claim(source_quote="The ledger is rewritten nightly")
        result = check_citation_support(claim, SOURCE_TEXT)
        self.assertEqual(result.verdict, "no")
        self.assertFalse(result.quote_found)

    def test_quote_matching_ignores_whitespace_differences(self) -> None:
        claim = make_claim(source_quote="The claim ledger\n   stays append-only")
        result = check_citation_support(claim, SOURCE_TEXT)
        self.assertTrue(result.quote_found)

    def test_empty_source_is_unknown(self) -> None:
        result = check_citation_support(make_claim(), "   ")
        self.assertEqual(result.verdict, "unknown")
        self.assertFalse(result.quote_found)

    def test_claim_without_content_tokens_is_unknown(self) -> None:
        claim = make_claim(standalone_claim_text="It is that and this.")
        result = check_citation_support(claim, SOURCE_TEXT)
        self.assertEqual(result.verdict, "unknown")
        self.assertTrue(result.quote_found)


class QuoteFallbackTests(unittest.TestCase):
    def test_supported_claims_are_kept_verbatim(self) -> None:
        supported = resolve_claim_support(make_claim(), SOURCE_TEXT)
        self.assertFalse(supported.fallback_applied)
        self.assertIsNone(supported.original_claim_text)
        self.assertEqual(
            supported.claim.standalone_claim_text,
            "The claim ledger stays append-only.",
        )

    def test_unsupported_claim_falls_back_to_quote(self) -> None:
        claim = make_claim(
            standalone_claim_text="Postgres replication requires three dedicated failover nodes."
        )
        supported = resolve_claim_support(claim, SOURCE_TEXT)
        self.assertTrue(supported.fallback_applied)
        self.assertEqual(
            supported.claim.standalone_claim_text,
            "The claim ledger stays append-only",
        )
        self.assertEqual(
            supported.original_claim_text,
            "Postgres replication requires three dedicated failover nodes.",
        )
        self.assertEqual(supported.claim.source_quote, claim.source_quote)

    def test_missing_quote_falls_back_to_source_text(self) -> None:
        claim = make_claim(source_quote="The ledger is rewritten nightly")
        supported = resolve_claim_support(claim, SOURCE_TEXT)
        self.assertTrue(supported.fallback_applied)
        self.assertEqual(supported.claim.standalone_claim_text, SOURCE_TEXT)
        self.assertEqual(supported.claim.source_quote, SOURCE_TEXT)

    def test_unknown_with_empty_source_cannot_fall_back(self) -> None:
        supported = resolve_claim_support(make_claim(), "")
        self.assertEqual(supported.check.verdict, "unknown")
        self.assertFalse(supported.fallback_applied)

    def test_injected_checker_is_used(self) -> None:
        def always_unknown(claim, source_text):
            return CitationCheckResult(
                verdict="unknown",
                quote_found=True,
                detail="mocked",
                method="mock-checker",
            )

        supported = resolve_claim_support(
            make_claim(), SOURCE_TEXT, checker=always_unknown
        )
        self.assertEqual(supported.check.method, "mock-checker")
        self.assertTrue(supported.fallback_applied)
        self.assertEqual(
            supported.claim.standalone_claim_text,
            "The claim ledger stays append-only",
        )

    def test_to_support_check_row_maps_fields(self) -> None:
        claim = make_claim(source_quote="The ledger is rewritten nightly")
        supported = resolve_claim_support(claim, SOURCE_TEXT)
        row = to_support_check_row(supported, claim_sequence=4)
        self.assertEqual(row["claim_sequence"], 4)
        self.assertEqual(row["support_verdict"], "no")
        self.assertEqual(row["check_method"], "s1-011-lexical-v1")
        self.assertFalse(row["quote_found"])
        self.assertTrue(row["fallback_applied"])
        self.assertEqual(
            row["original_claim_text"], "The claim ledger stays append-only."
        )
        self.assertTrue(row["detail"])


if __name__ == "__main__":
    unittest.main()
