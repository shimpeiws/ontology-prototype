# Canonical entities and append-only events

`ontology ingest` resolves each accepted observation through the workspace mapping and persists it into a SQLite canonical store at `<workspace>/.ontology/canon.db` (git-ignored with the rest of the runtime directory). The apply is one transaction: entity upserts, event inserts, and observation links commit together or roll back together. A store failure surfaces as a `CANON.STORE_ERROR` diagnostic and a nonzero exit, never a partial write. The apply runs only when both evidence artifacts — the snapshot bytes and the observations file — were persisted, so every canonical fact stays traceable to inspectable evidence; otherwise the run reports `CANON.SKIPPED` on top of the store error.

## Tables

| Table | Content |
| --- | --- |
| `entities` | Current state per `(entity_type, entity_id)`: the resolved property set plus first/last observation ids, the last observation time, and `properties_changed_at` — the observation time of the last upsert that actually changed the stored property set. An upsert applies only when the new observation is more recent than the stored one — `updated_at` moves forward in observation time, so out-of-order evidence can never regress current state — while `properties_changed_at` stays put when a re-observation carries identical properties. Equal observation times resolve by ingest id (the larger id wins), which keeps replays order-independent; two rows for one entity in a single run resolve to the first row. |
| `events` | Append-only business facts keyed by a stable `fact_key`. |
| `entity_observations` | Every accepted observation that resolved an entity, linked to that entity. |
| `event_observations` | Every accepted observation that asserted an event fact, including repeats of an already-known fact. |
| `projection_runs` | Frozen projection results (see [projections.md](projections.md)). |
| `diagnosis_runs` | Recorded diagnosis runs (see [diagnosis.md](diagnosis.md)). |
| `proposals` | Validated action proposals with payload digests (see [decisions.md](decisions.md)). |
| `decisions` | Append-only human approve/reject decisions; latest wins (see [decisions.md](decisions.md)). |
| `executions` | Append-only write-back attempts: an `attempting` intent row then an outcome row (`succeeded`/`failed`/`uncertain`), latest row wins (see [execution.md](execution.md)). |
| `fact_links` | Append-only associations recorded when a fact is appended: `possibly_corrects` (an earlier fact for the same event and subject carries a different `occurred_at`) and `correlated_intervention` (a persisted proposal targets the fact's subject). |
| `evaluation_runs` | Frozen outcome-evaluation reports (see [evaluation.md](evaluation.md)). |

## Event identity and append-only history

A fact key is `sha256` over the event type, subject entity/id, and `occurred_at` — never the observation or ingestion time. Re-ingesting the same snapshot therefore inserts no duplicate events; the repeat observation still links to the existing fact via `event_observations`, which is how a later run can see when and how often a fact was observed.

`UPDATE` and `DELETE` triggers on `events` make the append-only rule structural: history cannot be silently rewritten, and a source that omits a fact never deletes it. If a later snapshot reports a corrected timestamp, the correction lands as a *new* fact key (different `occurred_at`) alongside the original.

## Re-observation: corrections and intervention links

When `apply` appends a *new* fact it records, in the same transaction, how that fact relates to existing state — both relations land in `fact_links` (`fact_key`, `relation`, `target`, `ingest_id`, `observation_id`, `created_at`), append-only like `events`:

- `possibly_corrects` — the store sees another fact for the same event type and subject with a *different* `occurred_at`. A corrected timestamp and a legitimate second occurrence are indistinguishable at this layer, so both facts are kept and the ingest run surfaces a `CANON.POSSIBLE_CORRECTION` warning naming the conflicting times — one warning per new fact, however many earlier facts it conflicts with. Adjudicating which occurrence is real stays with the operator; nothing is overwritten.
- `correlated_intervention` — a persisted proposal targets the fact's subject (as its canonical `subject_entity`/`subject_id` or a `related_entities` ref), and the fact did not occur before the day the proposal was recorded (a fact dated earlier than the intervention is provably not a later outcome; same-day facts still link because date-only source times make intraday order unknowable). This links a later fact such as `InvoiceIssued` to the intervention for traceability. It is a **correlation, never a causal claim**, and it is recorded regardless of the proposal's decision or execution state — a fact observed after a rejected proposal or a failed execution is still worth tracing. The proposal id embeds its diagnosis run id, and live decision/execution state stays queryable through it.

Links are created only when a fact first lands; re-observing a known fact adds observation provenance (`event_observations`) but no new links. Before applying, ingest runs the same best-effort `proposals` index repair as `decide`/`execute`, so interventions recorded before the index existed still link. Two consequences of the append-only rule worth knowing: a conflicting pair of facts already written *before* `fact_links` existed produces no new diagnostic on re-ingest (re-observation does not re-evaluate links), and an unreadable `proposals` row skips intervention linking for that run rather than failing the ingest. Query a fact's associations with `store.fact_links(fact_key)` or everything pointing at a proposal with `store.links_for_target(proposal_id)`; every link also emits a `canonical.fact_linked` trace event.

`occurred_at` (when the business fact happened) and `observed_at` (when the system learned it) are stored as separate columns; a source-reported `observed_at` wins over the ingest timestamp.

## Scope notes

- Entity ids and `ref` values are opaque strings; `JobCompleted` and `InvoiceIssued` both carry the canonical `Job` subject, and no invoice ids are invented when the source has none.
- `entities` is a current-state read model ordered by observation time, while `events` is append-only history ordered by occurrence — the two can legitimately disagree (a job re-opened after completion keeps its `JobCompleted` fact while `status` moves back). Recording how the views relate is done (`fact_links`); adjudicating which fact is authoritative stays with the operator and is a later stage's job.
- v0.1 assumes a single writer per workspace, matching the evidence store.
- JSON property values are stored with datetimes as UTC ISO 8601 strings and dates as `YYYY-MM-DD`.

## Diagnostics

- `CANON.STORE_ERROR` — the canonical store could not be written. The store is a pluggable boundary, so any failure it raises — sqlite errors, IO errors, or a custom implementation's own exception — becomes this diagnostic, never a mid-run traceback.
- `CANON.SKIPPED` — warning: evidence persistence failed, so the canonical apply was skipped to avoid facts without inspectable provenance.
- `CANON.POSSIBLE_CORRECTION` — warning: a newly appended fact conflicts with an earlier fact for the same event and subject on `occurred_at` — a possible source correction or a distinct occurrence. Both facts are retained and linked (`fact_links`, relation `possibly_corrects`); the run still succeeds.
