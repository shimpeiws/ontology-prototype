# Snapshot ingestion and observation evidence

`ontology ingest <workspace> <snapshot>` reads a CSV or JSON snapshot, normalizes every record through one boundary, records each record as an observation with immutable evidence, and persists accepted entities/events into the canonical store (see [canonical.md](canonical.md)).

```bash
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs.csv --source jobs
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs.csv --source jobs --json
```

The snapshot format comes from the selected source's `format` (`csv` or `json`) in `mapping.sources[]`, not from the file extension. A mapping declaring a single source needs no flag; a multi-source mapping requires `--source <name>` (`INGEST.SOURCE_REQUIRED` when omitted), and an unknown name is `INGEST.UNKNOWN_SOURCE`. Each source's entity/event bindings apply only to snapshots ingested under that source.

Entity references cross source boundaries by design: a `ref` property or event subject resolves against the entities accepted in the same run plus every entity already in the canonical store — so an `invoices` row can reference a `Job` ingested earlier from `jobs`. The match is the exact (entity type, stable id) pair; a miss emits an `INGEST.UNRESOLVED_REF` warning on the record's observation and is never guessed.

The command exits `0` only when the run completes with no error-severity diagnostics; an invalid workspace, an unreadable/unparseable source, or any rejected record exits `1`.

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
| `canon.db` | SQLite canonical store: current entity state, append-only events, observation links, and frozen projection runs ([canonical.md](canonical.md)). |
| `trace.jsonl` | Append-only trace events: `observation.accepted`, `observation.rejected`, `ingest.completed`, `ingest.failed`, `canonical.event_recorded`, `canonical.fact_linked`, `canonical.applied`, `canonical.failed`, `projection.completed`, `projection.failed`. |

`.ontology/` is git-ignored: runtime snapshots, databases, and traces are never committed.

`ontology trace` reassembles these artifacts plus the later stages into one linked, read-only view — `ontology trace <workspace>` for the overview, `--subject Job/101` to follow one subject, `--run <id>` to inspect a single artifact, and `--export <path>` for a self-contained bundle. See [trace.md](trace.md).

## Re-ingesting later snapshots

The same command re-observes a source after it changes — run it again with the newer file (`examples/job-invoicing/data/jobs-later.csv` shows job 101 after its invoice was issued):

```bash
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs-later.csv --source jobs
```

Re-ingestion goes through the identical normalization and mapping path; only *new* facts append to `events` (the fact key covers event, subject, and `occurred_at`), while every observation — including repeats — lands in `event_observations` for provenance. A record absent from the later snapshot deletes nothing. When a later snapshot asserts an event the store already knows at a *different* `occurred_at`, the new fact is appended alongside the old one, the pair is linked in `fact_links` (`possibly_corrects`), and the run surfaces a `CANON.POSSIBLE_CORRECTION` warning — a corrected timestamp and a distinct occurrence are ambiguous, so both stay and a human resolves it. New facts also link to interventions on the same subject (`correlated_intervention`), keeping correlation explicitly distinct from causation. See [canonical.md](canonical.md#re-observation-corrections-and-intervention-links).

## Diagnostics

Ingestion diagnostics reuse the workspace diagnostic shape: `file` is the snapshot name and `path` is `record[<n>].<field>` (1-based record index). Codes:

- `INGEST.SOURCE_REQUIRED` — the mapping declares multiple sources and no `--source` was given.
- `INGEST.UNKNOWN_SOURCE` — `--source` names a source the mapping does not declare.
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
- `INGEST.UNRESOLVED_REF` — warning: a `ref` property value or event subject id matches no entity accepted in this run or stored in the canonical model; references resolve by exact stable id only, never by name or guess.
- `INGEST.STORE_ERROR` — evidence could not be persisted.
- `INGEST.EMPTY` — warning: the snapshot parsed but contained no records.
- `CANON.STORE_ERROR` — canonical entity/event persistence failed ([canonical.md](canonical.md)).
- `CANON.SKIPPED` — warning: canonical apply skipped because evidence persistence failed.
- `CANON.POSSIBLE_CORRECTION` — warning: a newly appended fact conflicts on `occurred_at` with an earlier fact for the same event and subject; both are retained and linked, and the run still succeeds.
