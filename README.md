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

| Task ID | Task | Phase | Category | Priority |
| --- | --- | --- | --- | --- |
| S1-001 | Research: Claude Retrieval Decision Note | Phase 1: Claude Retrieval | Research | High |
| S1-002 | Implement: Claude Transcript Extractor | Phase 1: Claude Retrieval | Backend | High |
| S1-003 | Implement: Messy Conversation Handling | Phase 1: Claude Retrieval | Backend | High |
| S1-004 | Verify: 10 Transcript Round Trips | Phase 1: Claude Retrieval | Evaluation | Medium |
| S1-005 | Design: Raw Store and Claim Ledger Schema | Phase 2: Knowledge Base Foundation | Data | High |
| S1-006 | Implement: Local-First Storage Layer | Phase 2: Knowledge Base Foundation | Backend | High |
| S1-007 | Implement: Ledger Query Paths | Phase 2: Knowledge Base Foundation | Backend | Medium |
| S1-008 | Create: Hand-Labeled Answer Key Dataset | Phase 3: Answer Key | Evaluation | High |
| S1-009 | Design: Claim Extraction Prompt Contract | Phase 4: Claim Extraction Pipeline | Backend | High |
| S1-010 | Implement: Per-Conversation Extraction Pipeline | Phase 4: Claim Extraction Pipeline | Backend | High |
| S1-011 | Implement: Citation-Fidelity Checker and Quote Fallback | Phase 4: Claim Extraction Pipeline | Evaluation | High |
| S1-012 | Automate: Evaluation Dataset Runs | Phase 4: Claim Extraction Pipeline | Evaluation | Medium |
| S1-013 | Measure: Coverage, Attribution, and Citation Fidelity | Phase 5: Measure and Iterate | Evaluation | High |
| S1-014 | Document: Sprint 1 Failure Modes Retro | Phase 5: Measure and Iterate | Documentation | Medium |

## Out of Scope for Sprint 1

- Other chatbots
- Notebook UI or generated notebook pages
- Concept dictionary or concept graph
- Contradiction and evolution detection
- Incremental notebook re-synthesis
- Human-edit handling

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

For local development without installing the package:

```bash
PYTHONPATH=src python3 -m chatnote capture https://claude.ai/share/<id>
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test*.py'
```
