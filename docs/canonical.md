# Canonical entities and append-only events

`ontology ingest` resolves each accepted observation through the workspace mapping and persists it into a SQLite canonical store at `<workspace>/.ontology/canon.db` (git-ignored with the rest of the runtime directory). The apply is one transaction: entity upserts, event inserts, and observation links commit together or roll back together. A store failure surfaces as a `CANON.STORE_ERROR` diagnostic and a nonzero exit, never a partial write. The apply runs only when both evidence artifacts — the snapshot bytes and the observations file — were persisted, so every canonical fact stays traceable to inspectable evidence; otherwise the run reports `CANON.SKIPPED` on top of the store error.

## Tables

| Table | Content |
| --- | --- |
| `entities` | Current state per `(entity_type, entity_id)`: the resolved property set plus first/last observation ids and the last observation time. An upsert applies only when the new observation is more recent than the stored one — `updated_at` moves forward in observation time, so out-of-order evidence can never regress current state. Equal observation times resolve by ingest id (the larger id wins), which keeps replays order-independent; two rows for one entity in a single run resolve to the first row. |
| `events` | Append-only business facts keyed by a stable `fact_key`. |
| `entity_observations` | Every accepted observation that resolved an entity, linked to that entity. |
| `event_observations` | Every accepted observation that asserted an event fact, including repeats of an already-known fact. |
| `projection_runs` | Frozen projection results (see [projections.md](projections.md)). |
| `diagnosis_runs` | Recorded diagnosis runs (see [diagnosis.md](diagnosis.md)). |
| `proposals` | Validated action proposals with payload digests (see [decisions.md](decisions.md)). |
| `decisions` | Append-only human approve/reject decisions; latest wins (see [decisions.md](decisions.md)). |

## Event identity and append-only history

A fact key is `sha256` over the event type, subject entity/id, and `occurred_at` — never the observation or ingestion time. Re-ingesting the same snapshot therefore inserts no duplicate events; the repeat observation still links to the existing fact via `event_observations`, which is how a later run can see when and how often a fact was observed.

`UPDATE` and `DELETE` triggers on `events` make the append-only rule structural: history cannot be silently rewritten, and a source that omits a fact never deletes it. If a later snapshot reports a corrected timestamp, the correction lands as a *new* fact key (different `occurred_at`) alongside the original — reconciliation is a later stage's job (#8).

`occurred_at` (when the business fact happened) and `observed_at` (when the system learned it) are stored as separate columns; a source-reported `observed_at` wins over the ingest timestamp.

## Scope notes

- Entity ids and `ref` values are opaque strings; `JobCompleted` and `InvoiceIssued` both carry the canonical `Job` subject, and no invoice ids are invented when the source has none.
- `entities` is a current-state read model ordered by observation time, while `events` is append-only history ordered by occurrence — the two can legitimately disagree (a job re-opened after completion keeps its `JobCompleted` fact while `status` moves back). Reconciling the two views is a later stage's job (#8).
- v0.1 assumes a single writer per workspace, matching the evidence store.
- JSON property values are stored with datetimes as UTC ISO 8601 strings and dates as `YYYY-MM-DD`.

## Diagnostics

- `CANON.STORE_ERROR` — the canonical store could not be written. The store is a pluggable boundary, so any failure it raises — sqlite errors, IO errors, or a custom implementation's own exception — becomes this diagnostic, never a mid-run traceback.
- `CANON.SKIPPED` — warning: evidence persistence failed, so the canonical apply was skipped to avoid facts without inspectable provenance.
