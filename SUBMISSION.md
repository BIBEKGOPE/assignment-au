# Submission

## Candidate
- **Name:** Bibek Gope
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** 4.5 hours

## How to run

From the repository root in Git Bash, with Python 3.11 or newer installed:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution ingest data/drop_1 --db ledger.db
python -m solution ingest data/drop_2 --db ledger.db
python -m solution report --out reports --db ledger.db
python -m pytest tests -q
```

The schemas default to `assignment-a-data/schemas`. The pipeline creates `ledger.db.key` beside
the database unless `PII_KEY` is set; preserve and restrict access to that key. Use separate
data, database, and report paths when testing different seeds. Do not commit generated data,
databases, reports, or key files.

## Design

Normalization and parsing are pure helpers; schema-driven ingestion and report generation handle
I/O. Each lead or event file is processed in one SQLite transaction. Its relative path and
SHA-256 content digest are recorded in `ingested_files`, so an unchanged file is skipped on
re-ingest. Leads are identified by `(workflow_id, HMAC-SHA256(phone))`; non-PII optional values
are stored in `attrs_json`. Customer names are omitted because no report needs them. The
`events` table is keyed by `event_id`, stores timestamps in UTC, and uses triggers to reject
updates and deletes.

Events are retained even when their lead has not arrived. At report time, normal events link by
`(workflow_id, phone_hash)` and overrides link through the call ID of another event. This lets
orphan events become linked after a later lead upload without changing the append-only ledger.
Reports derive call metrics, buckets, and final orphan/unknown-disposition counts from the
current database state and write deterministic, sorted CSV and JSON files.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Use HMAC-SHA256 for phone matching with a 32-byte key supplied as 64 hex characters in `PII_KEY`, or read/create a sibling `.key` file. | The valid phone space is small enough to brute-force plain hashes; deterministic keyed digests support matching without storing the phone. The key must be backed up and kept private. | Plain SHA-256 or storing phone numbers in plaintext. |
| Do not store `customer_name`. | It is PII and no requested report needs it. | Store it for possible future use. |
| Resolve event links at report time. | Events can arrive before leads; late linking lets the final reports heal orphans. | Permanently quarantine or classify events at ingestion. |
| For events with the same timestamp, use lexicographically greatest `event_id` as the tie-breaker. | Produces a deterministic disposition winner. | Depend on database row order. |
| Parse only the specified date formats with two-digit day/month components. Interpret naive timestamps as IST; count unreadable timestamps as malformed. | Strict regex/calendar validation avoids locale-dependent date parsing, and IST matches the input contract. | Lenient date parsing or treating naive timestamps as UTC. |
| Parse money with `Decimal` and round to two places using `ROUND_HALF_UP`. | Avoids binary floating-point money errors and uses conventional currency rounding. | `float`, `round()`, or truncation. |
| Preserve the first valid lead's `batch_id`; merge only non-null later values and apply defaults after merging. | Keeps the lead's origin stable while allowing later valid data to enrich it. A changed file with the same workflow and batch stem replaces its earlier rejection rows. | Move the lead to the newest batch or keep stale rejections. |
| A present but invalid required non-phone value is not properly handled by the current cleaner. It becomes `None` without rejection or warning; the supplied schemas do not currently define a required typed field that exercises this case. | This is an uncovered ambiguity and implementation limitation, not an intended validation rule. If such a field is added, reject the row with a field-specific invalid-value reason and add a test. | Accept the row silently or count a warning. |

## Failure modes considered

- Blank CSV rows are skipped and do not consume row numbers. Missing required fields and invalid
  present phones reject rows in schema order. Invalid optional values become null and count as
  warnings only for accepted rows.
- Malformed JSON, non-object events, missing event IDs, and unreadable timestamps are counted as
  malformed. Duplicate event IDs are ignored by the primary key, including events that have not
  linked to a lead yet.
- Re-ingesting a file with the same relative path and content hash is a no-op. Each file's data
  and counters commit together, so a failed file can be retried; a drop may be partially
  processed across files.
- Orphans are retained and recomputed at report time. Unknown disposition events are counted
  only after linking.
- Losing or changing the HMAC key prevents matching existing phone hashes. Key backup and access
  control are operational requirements.
- A present but invalid required non-phone field is a known gap if the schemas are extended.
  Event payload JSON is stored as supplied; future payload schemas must not put PII there unless
  it is protected.
- The large-scale performance target has not been benchmarked.

## Testing

The full suite passed with 132 tests using `python -m pytest tests -q`. Tests cover normalization,
schema aliases and header mapping, PII key/hash/masking, SQLite constraints, lead rejection and
merging rules, event malformation/deduplication/timestamps, report linking and business rules,
deterministic output, and CLI behavior. Generated-data end-to-end tests use seeds 42 and 7 to
check re-ingestion, incremental-versus-combined reports, orphan healing, and absence of plaintext
phone digits in SQLite files.

The large generator scale and production-style load/failure recovery were not benchmarked.

## Results

The full test suite completed in about 23 seconds in the current Windows environment. Generated
small-scale data tests passed for seeds 42 and 7. The generator intentionally includes malformed
JSONL records, which ingestion counts and skips. No large-scale timing or performance claim is
reported.

## If I had another day

1. Define and implement rejection behavior for present-but-invalid required non-phone values,
   with a regression test.
2. Benchmark the large generated dataset and profile ingestion/report hotspots.
3. Validate with representative, anonymized CSV exports from Excel (The current pipeline doesn’t read .xlsx directly, and the assignment calls for synthetic data—don’t use or commit real customer data.)
