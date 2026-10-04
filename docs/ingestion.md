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
- Values stay scalars until a mapping assignment applies its explicit `as` cast. Entity ids, subject ids, and `ref` properties are never cast — ids are opaque strings, so `0101` stays `0101` and a JSON number id is rejected rather than coerced.
- `as: date` parses an ISO 8601 date. `as: datetime` requires an explicit offset (`Z` or `±HH:MM`) and normalizes to UTC. A date-only value in a datetime context means midnight in `domain.policies.event_times.date_only_timezone` (`UTC` in schema v1). Naive datetimes are rejected, never guessed.
- `nan`/`inf` are not valid numbers; a boolean is never accepted as a number; nested objects/lists inside a record are rejected.

Each event binding's `when` condition is evaluated against the normalized record. When the condition holds, `occurred_at` is required; a source-reported `observed_at` wins over the ingest timestamp, which is the default.

## Observations and evidence

Every record becomes an observation carrying an observation id, the ingest id, the source name, the record index, the snapshot's content digest, a digest of the mapping definition plus the schema version, the observation timestamp, the normalized record, and — for accepted records — the resolved entity and event values.

A record that produces any error diagnostic is rejected as a whole; it is still recorded as evidence with its diagnostics, and no partially mapped facts are emitted.

Evidence lands under `<workspace>/.ontology/`:

| Path | Content |
| --- | --- |
| `snapshots/sha256-<digest>.<format>` | Raw source bytes, stored once under their content digest. The source file can change or be deleted afterwards; the stored snapshot is unaffected. |
| `observations/<ingest_id>.jsonl` | One JSON object per record, written atomically. |
| `trace.jsonl` | Append-only trace events: `observation.accepted`, `observation.rejected`, `ingest.completed`, `ingest.failed`. |

`.ontology/` is git-ignored: runtime snapshots, databases, and traces are never committed.

## Diagnostics

Ingestion diagnostics reuse the workspace diagnostic shape: `file` is the snapshot name and `path` is `record[<n>].<field>` (1-based record index). Codes:

- `INGEST.UNREADABLE_SOURCE`, `INGEST.SOURCE_TOO_LARGE`, `INGEST.ENCODING_ERROR` — the snapshot cannot be read.
- `INGEST.INVALID_SOURCE` — unparseable JSON, a non-array JSON top level, or a CSV without a header row.
- `INGEST.DUPLICATE_COLUMN` — a CSV header declares a column twice.
- `INGEST.INVALID_ROW` — a row is not a flat field mapping (extra cells, non-object JSON element).
- `INGEST.NON_SCALAR` — a field value is a nested object or list.
- `INGEST.MISSING_ID`, `INGEST.INVALID_ID` — an entity/subject id is absent/blank or not a string.
- `INGEST.MISSING_REQUIRED` — a `required: true` property is absent or blank.
- `INGEST.MISSING_TIME` — an applicable event has no `occurred_at`.
- `INGEST.INVALID_VALUE` — a cast fails (bad number, boolean, or type mismatch).
- `INGEST.INVALID_TIMESTAMP` — a malformed or timezone-less timestamp.
- `INGEST.STORE_ERROR` — evidence could not be persisted.
- `INGEST.EMPTY` — warning: the snapshot parsed but contained no records.
