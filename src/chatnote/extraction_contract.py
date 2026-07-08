from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .store import HEDGE_LEVELS, SPEAKER_ROLES, SPEECH_ACT_TYPES


PROMPT_VERSION = "s1-009-v1"

_REQUIRED_CLAIM_FIELDS = {
    "standalone_claim_text",
    "speaker_role",
    "speech_act_type",
    "hedge_level",
    "source_message_index",
    "source_quote",
}
_OPTIONAL_CLAIM_FIELDS = {
    "speaker_label",
    "source_block_index",
    "source_char_start",
    "source_char_end",
    "concept_tags",
}
_ALLOWED_CLAIM_FIELDS = _REQUIRED_CLAIM_FIELDS | _OPTIONAL_CLAIM_FIELDS

_PROMPT_INSTRUCTIONS = f"""\
You are ChatNote's claim extractor. Read ONE conversation transcript and emit
every ledger-ready claim it contains. Work on this transcript only; never use
outside knowledge, other conversations, or concept linking.

A claim is a single statement made by one speaker that is worth remembering:
a fact they asserted, a preference, a decision, an instruction, a question, a
plan, a todo, a correction, or a summary they gave. Rewrite each claim so it
stands alone without the surrounding conversation (resolve pronouns and
references), but never add information the speaker did not state and never
upgrade hedged language into confident language.

Return ONLY a JSON object with this exact shape and no other top-level keys:

{{
  "claims": [
    {{
      "standalone_claim_text": "<self-contained restatement of the claim>",
      "speaker_role": "<user|assistant|system|unknown>",
      "speech_act_type": "<{'|'.join(SPEECH_ACT_TYPES)}>",
      "hedge_level": "<{'|'.join(HEDGE_LEVELS)}>",
      "source_message_index": <index of the transcript message containing the claim>,
      "source_quote": "<verbatim span copied from that source message>",
      "source_block_index": <optional block index within the source message>,
      "source_char_start": <optional character offset where the quote starts>,
      "source_char_end": <optional character offset where the quote ends>,
      "speaker_label": "<optional display name if the transcript provides one>",
      "concept_tags": ["<optional free-form topic tags>"]
    }}
  ]
}}

Rules:
- speaker_role is who made the claim and must match the role shown for the
  source message.
- source_quote must be copied verbatim from the source message text; it is the
  evidence for the claim, so prefer the shortest span that fully supports it.
- If character offsets are provided they index into the source block text when
  source_block_index is present, otherwise into the source message text, and
  the offsets must reproduce source_quote exactly.
- hedge_level reflects the speaker's own confidence: none for plain
  assertions, low/medium/high for increasingly hedged statements, unknown when
  confidence cannot be judged.
- Emit an empty claims list if the transcript contains nothing worth keeping.
- Output nothing but the JSON object.
"""


class ExtractionValidationError(ValueError):
    """Raised when extraction output violates the S1-009 contract."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(
            "Extraction output failed contract validation: " + "; ".join(errors)
        )


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    standalone_claim_text: str
    speaker_role: str
    speech_act_type: str
    hedge_level: str
    source_message_index: int
    source_quote: str
    source_block_index: int | None = None
    source_char_start: int | None = None
    source_char_end: int | None = None
    speaker_label: str | None = None
    concept_tags: tuple[str, ...] = ()

    def to_ledger_row(
        self,
        *,
        transcript_id: str,
        conversation_id: str,
        claim_sequence: int,
        source_timestamp: str | None = None,
    ) -> dict[str, Any]:
        return {
            "transcript_id": transcript_id,
            "conversation_id": conversation_id,
            "claim_sequence": claim_sequence,
            "standalone_claim_text": self.standalone_claim_text,
            "speaker_role": self.speaker_role,
            "speaker_label": self.speaker_label,
            "speech_act_type": self.speech_act_type,
            "hedge_level": self.hedge_level,
            "source_message_index": self.source_message_index,
            "source_block_index": self.source_block_index,
            "source_char_start": self.source_char_start,
            "source_char_end": self.source_char_end,
            "source_quote": self.source_quote,
            "source_timestamp": source_timestamp,
            "concept_tags_json": json.dumps(list(self.concept_tags), ensure_ascii=False),
        }


def build_extraction_prompt(transcript: dict[str, Any]) -> str:
    """Render the deterministic one-conversation extraction prompt."""
    conversation = transcript.get("conversation", {})
    lines = [
        _PROMPT_INSTRUCTIONS,
        "Transcript:",
        f"conversation_id: {conversation.get('id', 'unknown')}",
    ]
    if conversation.get("title"):
        lines.append(f"title: {conversation['title']}")
    lines.append("")
    for message in transcript.get("messages", []):
        timestamp = message.get("timestamp") or "no timestamp"
        lines.append(f"[{message['index']}] {message['role']} ({timestamp}):")
        lines.append(message["text"])
        lines.append("")
    return "\n".join(lines)


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def parse_extraction_output(raw_output: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw_output, dict):
        return raw_output
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ExtractionValidationError(
            [f"Output is not valid JSON: {exc}"]
        ) from exc
    if not isinstance(payload, dict):
        raise ExtractionValidationError(["Output JSON must be an object."])
    return payload


def validate_extraction_output(
    payload: dict[str, Any], transcript: dict[str, Any]
) -> list[ExtractedClaim]:
    """Validate extraction output against the contract and the transcript.

    Collects every violation before raising so malformed output is fully
    visible in one pass.
    """
    errors: list[str] = []
    unexpected_keys = set(payload) - {"claims"}
    if unexpected_keys:
        errors.append(f"Unexpected top-level keys: {sorted(unexpected_keys)}")
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        errors.append('Output must contain a "claims" list.')
        raise ExtractionValidationError(errors)

    messages = {
        message["index"]: message for message in transcript.get("messages", [])
    }
    claims: list[ExtractedClaim] = []
    for position, raw_claim in enumerate(raw_claims):
        claim_errors = _validate_claim(raw_claim, position, messages)
        if claim_errors:
            errors.extend(claim_errors)
        else:
            claims.append(_build_claim(raw_claim))
    if errors:
        raise ExtractionValidationError(errors)
    return claims


def _validate_claim(
    raw_claim: Any, position: int, messages: dict[int, dict[str, Any]]
) -> list[str]:
    label = f"claims[{position}]"
    if not isinstance(raw_claim, dict):
        return [f"{label}: claim must be a JSON object."]

    errors = []
    unexpected = set(raw_claim) - _ALLOWED_CLAIM_FIELDS
    if unexpected:
        errors.append(f"{label}: unexpected fields {sorted(unexpected)}")
    missing = _REQUIRED_CLAIM_FIELDS - set(raw_claim)
    if missing:
        errors.append(f"{label}: missing required fields {sorted(missing)}")
        return errors

    for field_name in ("standalone_claim_text", "source_quote"):
        value = raw_claim[field_name]
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}: {field_name} must be a non-empty string.")
    if raw_claim["speaker_role"] not in SPEAKER_ROLES:
        errors.append(f"{label}: invalid speaker_role {raw_claim['speaker_role']!r}.")
    if raw_claim["speech_act_type"] not in SPEECH_ACT_TYPES:
        errors.append(
            f"{label}: invalid speech_act_type {raw_claim['speech_act_type']!r}."
        )
    if raw_claim["hedge_level"] not in HEDGE_LEVELS:
        errors.append(f"{label}: invalid hedge_level {raw_claim['hedge_level']!r}.")

    message_index = raw_claim["source_message_index"]
    if not isinstance(message_index, int) or isinstance(message_index, bool):
        errors.append(f"{label}: source_message_index must be an integer.")
        return errors
    source_message = messages.get(message_index)
    if source_message is None:
        errors.append(
            f"{label}: source_message_index {message_index} is not in the transcript."
        )
        return errors
    if (
        raw_claim["speaker_role"] in SPEAKER_ROLES
        and source_message["role"] != "unknown"
        and raw_claim["speaker_role"] != source_message["role"]
    ):
        errors.append(
            f"{label}: speaker_role {raw_claim['speaker_role']!r} does not match "
            f"source message role {source_message['role']!r}."
        )

    span_text = source_message["text"]
    block_index = raw_claim.get("source_block_index")
    if block_index is not None:
        blocks = source_message.get("blocks", [])
        if not isinstance(block_index, int) or isinstance(block_index, bool):
            errors.append(f"{label}: source_block_index must be an integer.")
        elif not 0 <= block_index < len(blocks):
            errors.append(
                f"{label}: source_block_index {block_index} is not in message "
                f"{message_index}."
            )
        else:
            span_text = blocks[block_index]["text"]

    char_start = raw_claim.get("source_char_start")
    char_end = raw_claim.get("source_char_end")
    if (char_start is None) != (char_end is None):
        errors.append(
            f"{label}: source_char_start and source_char_end must be provided together."
        )
    elif char_start is not None:
        if not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (char_start, char_end)
        ):
            errors.append(f"{label}: character offsets must be integers.")
        elif not 0 <= char_start < char_end <= len(span_text):
            errors.append(
                f"{label}: character offsets [{char_start}, {char_end}) are out of "
                "range for the source text."
            )
        elif span_text[char_start:char_end] != raw_claim["source_quote"]:
            errors.append(
                f"{label}: character offsets do not reproduce source_quote."
            )

    concept_tags = raw_claim.get("concept_tags", [])
    if not isinstance(concept_tags, list) or any(
        not isinstance(tag, str) or not tag.strip() for tag in concept_tags
    ):
        errors.append(f"{label}: concept_tags must be a list of non-empty strings.")

    speaker_label = raw_claim.get("speaker_label")
    if speaker_label is not None and (
        not isinstance(speaker_label, str) or not speaker_label.strip()
    ):
        errors.append(f"{label}: speaker_label must be a non-empty string when set.")
    return errors


def _build_claim(raw_claim: dict[str, Any]) -> ExtractedClaim:
    return ExtractedClaim(
        standalone_claim_text=raw_claim["standalone_claim_text"],
        speaker_role=raw_claim["speaker_role"],
        speech_act_type=raw_claim["speech_act_type"],
        hedge_level=raw_claim["hedge_level"],
        source_message_index=raw_claim["source_message_index"],
        source_quote=raw_claim["source_quote"],
        source_block_index=raw_claim.get("source_block_index"),
        source_char_start=raw_claim.get("source_char_start"),
        source_char_end=raw_claim.get("source_char_end"),
        speaker_label=raw_claim.get("speaker_label"),
        concept_tags=tuple(raw_claim.get("concept_tags", [])),
    )
