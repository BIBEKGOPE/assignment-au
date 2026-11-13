# AI Usage Disclosure

AI tools are allowed. We want to know **how** you used them, because using them well is a
skill. Honest answers are never held against you. Discovering undisclosed use is.

## Tools used
Copilot (AI assistant in VS Code).

## What I used them for
based on the readme suggestions ,
For Assignment A, Copilot generated `solution/normalize.py` from my detailed function
specification and created the parameterized pytest cases in `tests/test_normalize.py` from my
test requirements. Copilot also implemented `solution/schema.py` to load the supplied workflow
schemas and disposition catalogue and created `tests/test_schema.py`, including tests for renamed
headers and ignored columns. Copilot implemented `solution/pii.py` and `tests/test_pii.py` for
key management, deterministic phone HMACs, and masking. Copilot implemented `solution/db.py`
and `tests/test_db.py` for SQLite connection setup, schema initialization, and the append-only
event ledger. Copilot implemented `solution/ingest_events.py` and `tests/test_ingest_events.py`
for transactional JSONL ingestion, event deduplication, timestamp conversion, and PII phone
hashing. Copilot also implemented `solution/ingest_leads.py` and `tests/test_ingest_leads.py` for
transactional CSV ingestion and merging, plus `solution/report.py` and `tests/test_report.py` for
the business rules, event linking, aggregations, and five specified report outputs. Copilot
implemented `solution/pipeline.py`, `solution/cli.py`, and `solution/__main__.py`, with
`tests/test_pipeline_cli.py` covering sorted drop ingestion and command-line behavior. For
generated-data end-to-end coverage, Copilot added `tests/test_end_to_end.py` for seeds 42 and 7,
plus `tests/test_effective_disposition.py` and `tests/test_buckets.py` for the business rules.

## What I wrote or decided myself
I chose Assignment A. I specified the normalizer function behavior, input formats, edge cases,
the expected test cases, the schema loader's required data and header mapping behavior, and the
PII, database, ingestion, and report requirements. For the unspecified environment-variable
encoding, I chose to require `PII_KEY` to be a 64-character hex representation of a 32-byte key.
The lead-loader implementation assumes a changed file with the same workflow and batch stem
replaces that batch's earlier rejection rows, avoiding stale rows under the specified unique
key. I specified drop-processing order, CLI commands and defaults, and missing-directory behavior.
The report implementation groups calls by workflow, linked lead, and call ID; a linked initiated
event without a call ID is treated as its own call. I reviewed the generated implementation and
tests and ran the validation commands. For the new end-to-end tests, I selected assertions for
repeat ingestion, incremental-versus-combined output, orphan healing, and persisted phone privacy.

## Where the AI was wrong or unhelpful
An initial manual assertion expected a phone number containing full-width prefix digits followed
by ten valid ASCII digits to be rejected. That expectation was too strict: the specified behavior
keeps ASCII digits and ignores other characters, so the remaining ten digits form a valid number.
I changed the check to use an all-Arabic-Indic number, which must be rejected. A PowerShell test
snippet also mangled the literal rupee symbol when piped to Python; I noticed the unexpected
input in the output and reran the check using a Unicode escape. During the schema-loader work,
the compile and test runs passed without requiring a correction. The PII, database, and lead
ingestion implementations and their tests also passed without functional corrections.
The first event-ingestion draft included an unfinished helper and an incorrect file-stream
opening approach. I caught these while reviewing the draft, replaced them with streaming SHA-256
and a text wrapper around the already-open file, and then ran the tests successfully.
While editing the report tests, one patch attempt used a context line that was not present and
failed; I reread the file and applied the intended changes with the correct context. The report
tests and generated-data smoke run then passed. The pipeline and CLI tests passed without
requiring implementation corrections. The first run of the new plaintext-phone test failed
because it tried to parse every generated JSONL line as valid JSON; the assignment generator
deliberately emits malformed lines. I checked the ingestion behavior and updated the test to skip
malformed and non-object lines, matching the records the loader actually accepts. The targeted
tests then passed.

## How I verified AI-generated code
I read the generated code and ran `py_compile`, focused assertions for the normalizer's valid and
invalid formats, and `pytest tests -q` (132 tests passed). I also generated the small seed-42 assignment dataset, ingested both drops, and verified that all
five reports were created from the resulting database. The new subprocess tests generated both
drops for seeds 42 and 7 and verified idempotent ingestion, byte-identical reports,
incremental-versus-combined ingestion, orphan healing, and absence of plaintext phone numbers in
SQLite database files. Subprocess tests also exercised `python -m solution ingest` and `report`,
sorted drop processing, and the missing-directory error. The suite covers schema loading,
disposition flags, field order, first-match header mapping, ignored columns, key creation and
reuse, invalid keys, HMAC output, masking, SQLite connection settings, repeatable initialization,
append-only event enforcement, lead rejection order, blank-row numbering, warning counts,
duplicate merging, idempotent re-ingestion, counter invariants, transaction rollback, malformed
events, event deduplication, UTC timestamp storage, phone-less overrides, effective dispositions,
bucket priority, report sorting and headers, orphan/unknown ordering, and byte-identical reruns.

## Ongoing disclosure
For each subsequent completed coding prompt, I will update this file with the AI tools and work
involved, my own decisions, any incorrect or unhelpful output and how it was handled, and how I
verified the result.
