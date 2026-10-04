# Snapshot ingestion and observation evidence

`ontology ingest <workspace> <snapshot>` reads a CSV or JSON snapshot, normalizes every record through one boundary, and records each record as an observation with immutable evidence. Canonical entity/event persistence is a later stage; this document covers ingestion only.

```bash
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs.csv
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs.csv --json
```

The snapshot format comes from `mapping.source.format` (`csv` or `json`), not from the file extension. The command exits `0` only when the run completes with no error-severity diagnostics; an invalid workspace, an unreadable/unparseable source, or any rejected record exits `1`.

## The normalization boundary

Both formats parse into flat `field → scalar` records and then share one normalization path:

- Empty or whitespace-only strings (CSV blanks) and JSON nulls or absent keys become `null`.
- Values stay scalars until a mapping assignment applies its explicit `as` cast. Entity ids, subject ids, and `ref` properties are never cast — ids are opaque strings, so `0101` stays `0101` and a JSON number id is rejected rather than coerced. Ids are kept verbatim: `101 ` (trailing space) is a different id from `101` and emits an `INGEST.SUSPICIOUS_ID` warning.
- `as: date` parses a strict `YYYY-MM-DD` date (datetime strings, `20261001`, and week dates are rejected). `as: datetime` requires an explicit offset (`Z` or `±HH:MM`) and normalizes to UTC. A date-only value in a datetime context means midnight in `domain.policies.event_times.date_only_timezone` (`UTC` in schema v1). Naive datetimes are rejected, never guessed.
- `nan`/`inf` are not valid numbers; a boolean is never accepted as a number; integers too large to represent as a finite number are rejected; nested objects/lists inside a record are rejected.

Because CSV cells are always strings while JSON values are typed, the normalized `record` of an observation keeps format-dependent scalar types (`"42.5"` vs `42.5`). Cross-format equivalence holds on the resolved entity/event values, not on raw `record` scalars — do not diff `record` across formats.

Each event binding's `when` condition is evaluated against the normalized record. When the condition holds, `occurred_at` is required; a source-reported `observed_at` wins over the ingest timestamp, which is the default.

## Observations and evidence

Every record becomes an observation carrying an observation id, the ingest id, the source name, the record index, the snapshot's content digest, a digest of the mapping definition plus the schema version, the observation timestamp, the normalized record, and — for accepted records — the resolved entity and event values.

A record that produces any error diagnostic is rejected as a whole; it is still recorded as evidence with its diagnostics, and no partially mapped facts are emitted.

Evidence lands under `<workspace>/.ontology/`:

| Path | Content |
| --- | --- |
| `snapshots/sha256-<digest>.<format>` | Raw source bytes, stored once under their content digest. The source file can change or be deleted afterwards; the stored snapshot is unaffected. |
| `observations/<ingest_id>.jsonl` | One JSON object per record, written atomically. `ingest_id` is `ing-<utc-timestamp>-<digest8>-<run8>`; the run suffix keeps every run's file distinct and observation files are never overwritten. |
| `trace.jsonl` | Append-only trace events: `observation.accepted`, `observation.rejected`, `ingest.completed`, `ingest.failed`. |

`.ontology/` is git-ignored: runtime snapshots, databases, and traces are never committed.

## Diagnostics

Ingestion diagnostics reuse the workspace diagnostic shape: `file` is the snapshot name and `path` is `record[<n>].<field>` (1-based record index). Codes:

- `INGEST.UNREADABLE_SOURCE`, `INGEST.SOURCE_TOO_LARGE`, `INGEST.ENCODING_ERROR` — the snapshot cannot be read.
- `INGEST.INVALID_SOURCE` — unparseable JSON (including `NaN`/`Infinity` literals, which reject the whole snapshot rather than a single record), a non-array JSON top level, or a CSV without a header row.
- `INGEST.DUPLICATE_COLUMN` — a CSV header declares a column twice.
- `INGEST.INVALID_ROW` — a row is not a flat field mapping (extra cells, non-object JSON element).
- `INGEST.NON_SCALAR` — a field value is a nested object or list.
- `INGEST.MISSING_ID`, `INGEST.INVALID_ID` — an entity/subject id is absent/blank or not a string.
- `INGEST.SUSPICIOUS_ID` — warning: an id has leading/trailing whitespace (kept verbatim).
- `INGEST.MISSING_REQUIRED` — a `required: true` property is absent or blank.
- `INGEST.MISSING_TIME` — an applicable event has no `occurred_at`.
- `INGEST.INVALID_VALUE` — a cast fails (bad number, boolean, or type mismatch).
- `INGEST.INVALID_TIMESTAMP` — a malformed or timezone-less timestamp.
- `INGEST.STORE_ERROR` — evidence could not be persisted.
- `INGEST.EMPTY` — warning: the snapshot parsed but contained no records.
