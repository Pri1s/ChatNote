# S1-009 Claim Extraction Prompt Contract

## Purpose

This is the Sprint 1 contract for turning one parsed Claude transcript into
ledger-ready claims. It defines the prompt, the structured output shape, the
validation rules, and the mapping into `claim_ledger` from the S1-005 schema.

The contract is implemented in `src/chatnote/extraction_contract.py`:

- `PROMPT_VERSION` — current contract version, `s1-009-v1`, recorded on every
  extraction run.
- `build_extraction_prompt(transcript)` — deterministic prompt renderer.
- `parse_extraction_output(raw)` / `validate_extraction_output(payload, transcript)` —
  output parsing and contract validation.
- `ExtractedClaim.to_ledger_row(...)` — mapping into the claim ledger.

Cross-conversation reasoning, concept linking, and notebook prose are
explicitly out of scope. The extractor sees exactly one transcript per call.

## Prompt

`build_extraction_prompt` renders fixed instructions followed by the
transcript: the conversation ID, the title when available, and every message
as `[index] role (timestamp):` followed by the message text. The prompt asks
for standalone claims with attribution and forbids adding information or
upgrading hedged language. Rendering is deterministic so `prompt_sha256`
recorded on extraction runs identifies exactly what an extractor saw.

The prompt wording may change in later versions; consumers must key on
`PROMPT_VERSION` and `prompt_sha256`, not on prompt text. The extraction step
itself is a callable boundary (`pipeline.Extractor`), so the model, provider,
or structured-output mechanism can be swapped without changing this contract.

## Output Shape

The extractor must return a single JSON object with one top-level key:

```json
{
  "claims": [
    {
      "standalone_claim_text": "ChatNote stores raw transcripts immutably.",
      "speaker_role": "assistant",
      "speech_act_type": "fact",
      "hedge_level": "none",
      "source_message_index": 3,
      "source_quote": "raw transcripts are stored immutably",
      "source_block_index": 0,
      "source_char_start": 12,
      "source_char_end": 48,
      "speaker_label": null,
      "concept_tags": ["storage", "immutability"]
    }
  ]
}
```

An empty `claims` list is valid output for a conversation with nothing worth
keeping.

### Fields

| Field | Required | Rules |
| --- | --- | --- |
| `standalone_claim_text` | Yes | Non-empty; self-contained restatement with references resolved; must not add or de-hedge content. |
| `speaker_role` | Yes | `user`, `assistant`, `system`, or `unknown`; must match the role of the source message (unless that role is `unknown`). |
| `speech_act_type` | Yes | `fact`, `preference`, `decision`, `instruction`, `question`, `plan`, `todo`, `correction`, `summary`, or `other`. |
| `hedge_level` | Yes | `none`, `low`, `medium`, `high`, or `unknown`; reflects the speaker's own confidence. |
| `source_message_index` | Yes | Integer index of an existing transcript message. |
| `source_quote` | Yes | Non-empty verbatim span copied from the source message; the shortest span that supports the claim. |
| `source_block_index` | No | Integer index of an existing block within the source message. |
| `source_char_start` / `source_char_end` | No | Provided together; offsets into the block text when `source_block_index` is set, otherwise the message text; the slice must reproduce `source_quote` exactly. |
| `speaker_label` | No | Non-empty display name when the transcript provides one. |
| `concept_tags` | No | List of non-empty free-form strings; no concept dictionary or linking. |

Unknown fields — top-level or per claim — are contract violations.

## Validation

`validate_extraction_output` checks the whole payload and raises
`ExtractionValidationError` carrying every violation at once, so a malformed
extraction is fully visible in a single failed run rather than one error at a
time. Validation is structural and transcript-relative (field presence, enum
membership, pointer ranges, offset/quote agreement, attribution/role match).
Judging whether the claim wording is actually supported by the quote is the
S1-011 citation support check, which runs after validation.

## Ledger Mapping

`ExtractedClaim.to_ledger_row` maps one validated claim onto one
`claim_ledger` row:

| Contract field | Ledger column |
| --- | --- |
| `standalone_claim_text` | `standalone_claim_text` |
| `speaker_role` / `speaker_label` | `speaker_role` / `speaker_label` |
| `speech_act_type` / `hedge_level` | `speech_act_type` / `hedge_level` |
| `source_message_index` / `source_block_index` | same columns |
| `source_char_start` / `source_char_end` | same columns |
| `source_quote` | `source_quote` |
| `concept_tags` | `concept_tags_json` (JSON array) |

The pipeline supplies `transcript_id`, `conversation_id`, `claim_sequence`
(claim order within the run), and `source_timestamp` (copied from the source
message when available). `supersedes_claim_id` is not produced by extraction
in Sprint 1.

## Synthetic Examples

Hedged user preference (valid):

```json
{
  "standalone_claim_text": "The user would probably prefer SQLite over Postgres for local storage.",
  "speaker_role": "user",
  "speech_act_type": "preference",
  "hedge_level": "medium",
  "source_message_index": 0,
  "source_quote": "I'd probably lean SQLite over Postgres here",
  "concept_tags": ["storage"]
}
```

Rejected claim and the errors reported for it:

```json
{
  "standalone_claim_text": "",
  "speaker_role": "moderator",
  "speech_act_type": "belief",
  "hedge_level": "none",
  "source_message_index": 99,
  "source_quote": "…"
}
```

- `claims[0]: standalone_claim_text must be a non-empty string.`
- `claims[0]: invalid speaker_role 'moderator'.`
- `claims[0]: invalid speech_act_type 'belief'.`
- `claims[0]: source_message_index 99 is not in the transcript.`

Runnable synthetic examples live in `tests/fixtures/extraction_output_valid.json`
and `tests/test_extraction_contract.py`.
