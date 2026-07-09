# ChatNote

ChatNote is a local-first knowledge-base project for turning selected Claude
conversations into faithful, attributed records. Sprint 1 focuses on capture:
retrieving a full Claude transcript, storing the raw source, extracting
ledger-ready claims, and measuring citation fidelity against a hand-labeled
answer key.

## Planning Sources

- Product/architecture source: [ChatNote Google Doc](https://docs.google.com/document/d/1w9Aj61fHGprCCzEUi3tYufOHyxS1yK82fTFWcNXBFdU)
- Notion project page: [ChatNote](https://app.notion.com/p/3959d08adba28138ad40e8b3e78fdc6e)
- Notion task tracker: [Development Tasks](https://app.notion.com/p/f2b9215aba524845bb353516ae7cea32)

## Sprint 1 Goal

Retrieve the full context of user-selected Claude conversations and build the
first version of the knowledge base: an append-only claim ledger backed by an
immutable raw transcript store.

Definition of done: a selected Claude conversation can go from open in browser
to raw stored transcript to extracted, attributed claims in the ledger, with a
measured citation-fidelity score against a hand-labeled answer key.

## Sprint 1 Tasks

The Notion task tracker is the source of truth; this table mirrors its current
Sprint 1 scope.

| Task ID | Task | Phase | Category | Priority |
| --- | --- | --- | --- | --- |
| S1-001 | Research: Claude Retrieval Decision Note | Phase 1: Claude Retrieval | Research | High |
| S1-002 | Implement: Claude Transcript Extractor | Phase 1: Claude Retrieval | Backend | High |
| S1-004 | Verify: MVP Transcript Smoke Check | Phase 1: Claude Retrieval | Evaluation | Medium |
| S1-005 | Design: Raw Store and Claim Ledger Schema | Phase 2: Knowledge Base Foundation | Data | High |
| S1-006 | Implement: Local-First Storage Layer | Phase 2: Knowledge Base Foundation | Backend | High |
| S1-007 | Implement: Ledger Query Paths | Phase 2: Knowledge Base Foundation | Backend | Medium |
| S1-009 | Design: Claim Extraction Prompt Contract | Phase 4: Claim Extraction Pipeline | Backend | High |
| S1-010 | Implement: Per-Conversation Extraction Pipeline | Phase 4: Claim Extraction Pipeline | Backend | High |
| S1-011 | Implement: Citation Support Check and Quote Fallback | Phase 4: Claim Extraction Pipeline | Backend | High |

Sprint 2 closes the functional-MVP gaps: S2-001 (OpenRouter model-backed claim
extractor) and S2-002 (one-shot `chatnote run` command). The previously
deferred manual-evaluation work (messy conversation handling, 10-transcript
round trips, hand-labeled answer key, evaluation dataset runs,
coverage/attribution/fidelity scoring, failure-modes retro) sits un-numbered
in the tracker Backlog.

## Out of Scope for Sprint 1

- Other chatbots
- Notebook UI or generated notebook pages
- Concept dictionary or concept graph
- Contradiction and evolution detection
- Incremental notebook re-synthesis
- Human-edit handling

## MVP Quickstart (Sprint 2)

Sprint 2 closes the MVP gap: claims are extracted by a live model through
OpenRouter's OpenAI-compatible API, and one command runs the whole flow from
share link to claim ledger.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .

export OPENROUTER_API_KEY=sk-or-...        # https://openrouter.ai/keys
export CHATNOTE_MODEL=anthropic/claude-sonnet-5   # any OpenRouter model slug

chatnote run https://claude.ai/share/<id>
chatnote query claims --conversation <id>
```

`chatnote run` chains capture → store ingest → claim extraction and prints the
transcript ID, claim counts, citation support verdicts, and ready-to-paste
query commands. If a stage fails it reports what did complete (capture file
paths, the stored transcript ID) so nothing has to be redone.

For the zero-setup version, `main.py` at the repo root loads `.env` itself (no
exports needed), runs the same flow, and prints the extracted claims at the
end. Copy `.env.example` to `.env`, fill in your key and model, then:

```bash
python3 main.py https://claude.ai/share/<id>   # or run bare to be prompted
```

Extra flags are forwarded to `chatnote run`, so `--model`, `--claims-json`,
`--db-path`, and `--output-dir` all work there too.

The model is plug-and-play by design — swap `CHATNOTE_MODEL` (or pass
`--model`) to experiment with different models. There is no default model: an
unset model is a clear configuration error instead of a confusing API failure.

Environment variables:

| Variable | Required | Meaning |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | yes (for model extraction) | OpenRouter API key sent as the bearer token. |
| `CHATNOTE_MODEL` | yes, unless `--model` is passed | Model slug for extraction, e.g. `anthropic/claude-sonnet-5`. |
| `CHATNOTE_LLM_BASE_URL` | no | OpenAI-compatible base URL; defaults to `https://openrouter.ai/api/v1`. Point it at a local Ollama/vLLM server to run without OpenRouter. |

`--claims-json <file>` remains the offline/testing path on both `chatnote run`
and `chatnote extract`: it feeds pre-generated extraction output through the
same pipeline with no network call. The citation support check stays
deterministic and lexical in the MVP.

## Local CLI

Sprint 1 capture starts with a single public Claude shared snapshot link. The
CLI intentionally rejects private Claude chat URLs, non-Claude URLs, and any
capture path that would require private endpoints or automated logged-in DOM
scraping.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
chatnote capture https://claude.ai/share/<id>
```

By default, capture writes private local artifacts under ignored paths:

- `data/raw/`: immutable raw shared-snapshot source, usually Claude's public
  snapshot JSON and occasionally rendered/shared HTML fixtures
- `data/transcripts/`: parsed transcript JSON

The transcript schema includes source metadata, conversation metadata, ordered
messages, structured blocks, nullable timestamps, provenance, and warnings for
snapshot limitations such as missing timestamps, attachment placeholders, or
unsupported tool-call data.

For current Claude share pages, the CLI first tries the public
`/api/chat_snapshots/<id>` payload because the HTML page can be only the Claude
web app shell. If Claude returns a challenge instead of transcript data, the CLI
reports that as a fetch error instead of saving an empty transcript.

## Knowledge Base Storage and Extraction

The Sprint 1 knowledge-base foundation lives beside the capture CLI:

- `chatnote store init` creates the local SQLite store (default
  `data/chatnote.db`, ignored by git) from the schema contracts in
  [docs/s1-005-schema-v1.sql](docs/s1-005-schema-v1.sql) and
  [docs/s1-011-citation-support-v1.sql](docs/s1-011-citation-support-v1.sql).
  Raw artifacts and transcripts are immutable; extraction runs, claim ledger
  rows, and citation support checks are append-only, enforced by triggers.
- `chatnote store ingest <raw> <transcript>` registers one capture output pair
  and stores the parsed transcript records.
- `chatnote extract <transcript-id>` runs one transcript through the claim
  extraction pipeline. By default it calls the OpenRouter model from
  `--model`/`CHATNOTE_MODEL` (see the MVP quickstart); with
  `--claims-json <file>` it uses the Sprint 1 file-backed extractor
  (pre-generated output JSON) so the pipeline runs without a live model call.
  Output is validated against the
  [S1-009 prompt contract](docs/s1-009-claim-extraction-prompt-contract.md),
  each claim gets a citation support verdict (`yes`, `partial`, `no`,
  `unknown`), and unsupported claims are stored as direct quotes instead of
  unsupported rewrites. Malformed output records a visible failed run and
  writes no claims.
- `chatnote query transcripts|claims|runs` inspects stored records. Claims can
  be filtered by `--conversation`, `--speaker`, and `--speech-act`, and every
  result keeps its source pointer back to the raw message. Add `--json` for
  machine-readable output.

```bash
chatnote store init
chatnote store ingest data/raw/<file>.json data/transcripts/<file>.json
chatnote extract <transcript-id>
chatnote query claims --speaker user --speech-act preference
```

For local development without installing the package:

```bash
PYTHONPATH=src python3 -m chatnote capture https://claude.ai/share/<id>
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test*.py'
```
