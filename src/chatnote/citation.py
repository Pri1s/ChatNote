from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Callable

from .extraction_contract import ExtractedClaim


CHECK_METHOD_LEXICAL_V1 = "s1-011-lexical-v1"

_FULL_SUPPORT_COVERAGE = 0.75
_PARTIAL_SUPPORT_COVERAGE = 0.4

_STOPWORDS = frozenset(
    "a an and are as at be but by for if in into is it its of on or that the "
    "their then there these this to was were will with".split()
)


@dataclass(frozen=True, slots=True)
class CitationCheckResult:
    verdict: str  # yes | partial | no | unknown
    quote_found: bool
    detail: str
    method: str = CHECK_METHOD_LEXICAL_V1


@dataclass(frozen=True, slots=True)
class SupportedClaim:
    """A claim after the citation support check and optional quote fallback."""

    claim: ExtractedClaim
    check: CitationCheckResult
    fallback_applied: bool
    original_claim_text: str | None


CitationChecker = Callable[[ExtractedClaim, str], CitationCheckResult]


def check_citation_support(claim: ExtractedClaim, source_text: str) -> CitationCheckResult:
    """Deterministic lexical support check for one claim against its source.

    The quote is treated as the evidence: a claim can only be supported when
    its source_quote is a verbatim span of the source text. Claim wording is
    then compared to the source by content-token coverage.
    """
    if not source_text.strip():
        return CitationCheckResult(
            verdict="unknown",
            quote_found=False,
            detail="Source message text is unavailable, so support cannot be judged.",
        )

    quote_found = _normalize(claim.source_quote) in _normalize(source_text)
    if not quote_found:
        return CitationCheckResult(
            verdict="no",
            quote_found=False,
            detail="source_quote is not a verbatim span of the source message text.",
        )

    claim_tokens = _content_tokens(claim.standalone_claim_text)
    if not claim_tokens:
        return CitationCheckResult(
            verdict="unknown",
            quote_found=True,
            detail="Claim text has no content tokens to compare against the source.",
        )
    source_tokens = _content_tokens(source_text)
    coverage = len(claim_tokens & source_tokens) / len(claim_tokens)
    detail = f"{coverage:.2f} of claim content tokens appear in the source message."
    if coverage >= _FULL_SUPPORT_COVERAGE:
        return CitationCheckResult(verdict="yes", quote_found=True, detail=detail)
    if coverage >= _PARTIAL_SUPPORT_COVERAGE:
        return CitationCheckResult(verdict="partial", quote_found=True, detail=detail)
    return CitationCheckResult(verdict="no", quote_found=True, detail=detail)


def resolve_claim_support(
    claim: ExtractedClaim,
    source_text: str,
    *,
    checker: CitationChecker | None = None,
) -> SupportedClaim:
    """Run the support check and fall back to a direct quote when needed.

    Claims judged unsupported ("no") or unjudgeable ("unknown") are rewritten
    to carry verbatim source text instead of an unsupported restatement. The
    original claim text is preserved in the support record so nothing is lost.
    """
    check = (checker or check_citation_support)(claim, source_text)
    if check.verdict not in ("no", "unknown"):
        return SupportedClaim(
            claim=claim, check=check, fallback_applied=False, original_claim_text=None
        )

    fallback_text = claim.source_quote if check.quote_found else source_text.strip()
    if not fallback_text.strip():
        return SupportedClaim(
            claim=claim, check=check, fallback_applied=False, original_claim_text=None
        )
    fallback_claim = replace(
        claim,
        standalone_claim_text=fallback_text,
        source_quote=fallback_text if not check.quote_found else claim.source_quote,
    )
    return SupportedClaim(
        claim=fallback_claim,
        check=check,
        fallback_applied=True,
        original_claim_text=claim.standalone_claim_text,
    )


def to_support_check_row(supported: SupportedClaim, *, claim_sequence: int) -> dict[str, Any]:
    """Map a resolved claim to a claim_support_checks insert row."""
    return {
        "claim_sequence": claim_sequence,
        "support_verdict": supported.check.verdict,
        "check_method": supported.check.method,
        "quote_found": supported.check.quote_found,
        "fallback_applied": supported.fallback_applied,
        "original_claim_text": supported.original_claim_text,
        "detail": supported.check.detail,
    }


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _content_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {token for token in tokens if token not in _STOPWORDS}
