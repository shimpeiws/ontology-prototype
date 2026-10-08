# Workspace definitions and validation

A workspace is a directory containing four YAML definition files. The FDE checks these files in; loading them is data-only — no environment interpolation, no external includes, no Python or SQL execution. Files must be UTF-8 and at most 1 MiB each. Anchors/aliases are permitted; a merge key that overrides an explicit key is reported as `WORKSPACE.DUPLICATE_KEY`, never silently merged.

| File | Content |
| --- | --- |
| `domain.yaml` | Typed entity/event model, projections, and explicit policies |
| `mapping.yaml` | Declarative mapping from source snapshots to entities and events |
| `evaluation.yaml` | Outcome contract: interval, aggregation, baseline, strict target, guardrails, declared metrics |
| `agent.yaml` | Diagnosis task: projection to inspect, deliverables, permitted action schemas |

Validate a workspace with `ontology check <dir>` (or `load_workspace(<dir>)` from Python). Validation collects diagnostics; it never fails fast. Each diagnostic carries a severity (`error`/`warning`), a stable code, the file, a dotted path into the document, and a hint where the fix is guessable (for example, `did you mean 'Person'?`). Exit status is `0` only with zero errors; warnings never fail a workspace.

## Common header

Every file starts with an explicit schema version and kind. A bad header skips deeper validation of that file.

```yaml
schema_version: 1
kind: domain | mapping | evaluation | agent
```

`schema_version` must be the integer `1`. `kind` must match the file name.

## domain.yaml

```yaml
domain:
  name: job-invoicing
  policies:
    entity_id_type: string          # ids are opaque strings, never coerced
    event_times:
      occurrence_field: occurred_at # when the business fact happened
      observation_field: observed_at # when the system learned it
      date_only_timezone: UTC        # YYYY-MM-DD means midnight UTC
  entities:
    Job:
      properties:
        status:   { type: string, required: true }
        assignee: { type: ref, ref: Person }
  events:
    JobCompleted: { subject: Job }
  projections:
    CompletedButNotInvoiced:
      subject: Job
      select:
        required_events: [JobCompleted]
        absent_events: [InvoiceIssued]
        elapsed: { event: JobCompleted, comparator: gt, value: 2, unit: days }
```

- Property `type` is one of `string`, `integer`, `number`, `boolean`, `date`, `datetime`, `ref`.
- `ref` properties require `ref: <entity name>`; references resolve to entities only (never events or projections).
- Every event and projection requires a `subject` entity.
- Every projection declares its candidate selection under `select`:
  - `required_events` — non-empty list of event names; every one must have occurred by `as_of`. All named events must be declared with the projection's `subject` and must not overlap `absent_events`.
  - `absent_events` — optional list of event names; any occurrence at or before `as_of` excludes the subject.
  - `elapsed` — optional as-of-relative duration comparison `{ event, comparator, value, unit }`: `event` is optional, defaults to the first `required_events` entry, and must be one of `required_events`; `comparator` is one of `lt`, `lte`, `gt`, `gte`; `value` is a non-negative number; `unit` is one of `seconds`, `minutes`, `hours`, `days`.
  - `where` — optional condition over the subject entity's properties, using the same restricted condition language as mapping `when:` clauses.
- Every event carries an occurrence time and an observation time. Schema v1 fixes the canonical field names: `occurrence_field` must be `occurred_at` and `observation_field` must be `observed_at` — the policy declares them explicitly so all four files agree on the same temporal contract. `date_only_timezone` must be `UTC`: a date-only value means midnight UTC.
- `entity_id_type` must be `string` — entity ids are never cast to numbers.

## mapping.yaml

```yaml
mapping:
  sources:
    - name: jobs                         # stable source name; selected at ingest
      format: csv                        # csv | json
      entities:
        - entity: Job
          id: { field: id }              # id stays a string; 'as' is forbidden here
          properties:
            status:   { field: status }
            assignee: { field: assignee }    # ref target: the raw string id
            amount:   { field: amount, as: number }
      events:
        - event: JobCompleted
          subject: { entity: Job, id: { field: id } }
          when: { op: and, args: [ { op: eq, field: status, value: completed }, { op: not_null, field: completed_at } ] }
          occurred_at: { field: completed_at, as: datetime }
          observed_at: { field: seen_at, as: datetime }   # optional
    - name: invoices
      format: csv
      entities:
        - entity: Invoice
          id: { field: id }
          properties:
            job: { field: job }          # ref to a Job id ingested from any source
      events:
        - event: InvoiceIssued
          subject: { entity: Job, id: { field: job } }
          occurred_at: { field: issued_at, as: datetime }
```

- `sources` is a non-empty list; each entry carries its own `name`, `format`, `entities`, and `events`. Source names must be unique (`MAPPING.DUPLICATE_SOURCE`).
- The earlier single-source form — top-level `source: { name, format }` plus top-level `entities`/`events` — still loads unchanged and is normalized to a one-element `sources` list. Mixing the two forms is an error (`MAPPING.MIXED_SOURCES`).
- Entity `ref` properties and event subjects may point at entities bound by a *different* source; at ingest the reference resolves against the canonical store by exact stable id, with unresolved references reported as `INGEST.UNRESOLVED_REF` warnings (see [ingestion.md](ingestion.md)).
- An assignment is `{ field: <column> }` or `{ literal: <scalar> }` — exactly one — plus optional `as` cast.
- `as` must equal the target property's type and is required for non-`string` scalar properties. It is forbidden on `ref` properties and on entity ids.
- Literals for entity ids, subject ids, and event times must be strings — ids and times are never numeric literals.
- `occurred_at` is required and `observed_at` optional on each event binding; both require `as: date` or `as: datetime`. The key names are the ones pinned by `domain.policies.event_times`.
- `subject.entity` must equal the event's declared subject in the domain.
- Every `required: true` domain property of a bound entity must have an assignment; a binding that can never produce a complete entity fails validation.
- Entity names, event names, and property names must exist in the domain.

### Condition language

`when` is an allowlisted tree — it is data, not code:

- Comparisons: `{ op: eq|ne|lt|lte|gt|gte, field: <name>, value: <scalar> }`
- Sets: `{ op: in|not_in, field: <name>, values: [<scalar>, ...] }`
- Null checks: `{ op: is_null|not_null, field: <name> }`
- Logic: `{ op: and|or, args: [<condition>, ...] }`, `{ op: not, arg: <condition> }`

Unknown operators, missing/extra operands, non-scalar values, and nesting beyond 32 levels are errors. Nothing in a condition is ever evaluated as Python or SQL.

## evaluation.yaml

```yaml
evaluation:
  name: completion-to-invoice-latency
  interval: { start_event: JobCompleted, end_event: InvoiceIssued }
  metric: duration        # v1 supports duration only
  aggregation: median     # median | mean | min | max | count
  unit: days              # seconds | minutes | hours | days
  baseline: { value: 9 }
  target: { comparator: lt, value: 4 }
  metrics:                            # the rates a guardrail may name
    invoice_error_rate:
      entity: Invoice                 # the counted domain entity
      ref: job                        # ref property linking it to the interval subject
      flag: error                     # boolean predicate — or `where: <condition>`
      # noun: errors                  # optional report noun for the numerator
  guardrails:
    - { name: invoice-error-rate, metric: invoice_error_rate, comparator: not_increase }
```

- `start_event` and `end_event` must be different domain events sharing one subject.
- `target.comparator` is `lt | lte | gt | gte`; `lt` is strict, so a 4-day median misses a `< 4` target.
- Guardrail comparator `not_increase` compares against the baseline and takes no `value`; other comparators require a numeric `value`.
- Each `metrics` entry declares a per-cohort rate: `entity` and `ref` must name a domain entity and a ref-typed property on it, and exactly one predicate is required — `flag` (a declared boolean property) or `where` (a condition over the entity's properties). The ref's target must match the interval's subject entity, or the guardrail reports `unavailable` at evaluate time.
- `ontology evaluate` executes the contract — see [evaluation.md](evaluation.md) for cohort, pairing, and guardrail-evidence rules.

## agent.yaml

```yaml
agent:
  name: billing-delay-diagnosis
  projection: CompletedButNotInvoiced
  task:
    goal: Identify likely causes of delayed invoicing.
    deliverables: [explanation, hypotheses, follow_up_questions, action_proposals]
  permitted_actions:
    SendReminder:
      params:
        target:         { type: ref, ref: Person, required: true }
        related_entity: { type: ref, ref: Job,    required: true }
        reason:         { type: string,           required: true }
```

- `projection` must be a projection declared in the domain.
- Deliverables are `explanation`, `hypotheses`, `follow_up_questions`, `action_proposals`; `action_proposals` requires at least one permitted action.
- Action `params` use the same typed property spec as the domain; `ref` targets must be entities.

## Boundary

Apps call `ontology_core.load_workspace` and present its diagnostics; they do not re-validate, and writes go through `ontology_core.save_workspace`. `ontology_core` imports nothing from apps and needs only PyYAML. When `domain.yaml` itself fails, cross-file reference checks are skipped with a `WORKSPACE.CROSS_VALIDATION_SKIPPED` warning so diagnostics stay actionable instead of cascading.

## Saving

`ontology_core.save_workspace(path, texts, base_digest=...)` activates a draft bundle as the workspace on disk — the shared persistence the API's save endpoint exposes and the CLI interoperates with. The flow is fixed:

1. **Whole-bundle validation first.** The draft texts run through the same restricted loader and validators as `load_workspace` / `ontology check`; an invalid bundle writes nothing and reports the standard diagnostics.
2. **Base digest check.** The on-disk bundle is read byte-exact and its digest compared against `base_digest` — the digest the open/inspect call returned. A mismatch (an external or CLI edit, or a file that appeared or disappeared) reports a `conflict` carrying the current on-disk digest; nothing is written.
3. **Atomic activation.** All four files are staged as synced temporary files inside the workspace directory before the first rename, then each target is replaced with `os.replace` — per-file atomic, and never writing through a symlink. A mid-commit failure restores every already-activated file to its pre-save bytes, so a failed save never leaves a mixed old/new bundle; an incomplete rollback is reported in the failure message. Replaced files keep their permission mode.
4. **Mid-activation re-verification.** `os.replace` is a blind overwrite, so before each rename the target's bytes are re-checked against what the digest read: a cross-process write that lands between the digest check and activation aborts the save as `conflict` (with rollback) instead of being silently lost. A residual window remains between that re-check and the rename itself — single-digit microseconds per file — and is accepted for v0.2 without inter-process locking.
5. **Symlinked definition files are refused** — a save never writes through or silently replaces a link the operator placed.

Save outcomes are `saved` / `invalid` / `conflict` / `failed` (see `SaveResult`). Concurrent in-process saves of one directory are serialized; the digest check plus the per-file re-verification form the contract against cross-process writers — a local single-user tool does not take out inter-process locks.

The atomicity above is writer-facing. A *reader* that loads the bundle while activation is in flight can observe a mixed old/new generation: `os.replace` commits one file at a time and there is no read-side locking. That window is microseconds per file, and a reader that lands in it sees valid YAML per file — the cross-file consistency guarantee is on writers (via the digest check), not on mid-commit readers. Callers that need a consistent generation should re-read after the save reports.

Only the four definition files are ever written: a save cannot touch canonical entities/events, observations, traces, proposals, approvals, or any other file in the directory, and it never creates the workspace directory.

### Serialization and round-trip

Saved bytes are exactly the supplied draft text encoded as UTF-8 — the core does not re-serialize the parsed model. A UI editor that edits YAML through a CST-preserving document therefore keeps comments, key order, quoting, and unmodeled fields byte-exact. The round-trip limits live at the editor, not the loader: formatting *inside a node the editor rewrites* follows that library's emission, and anchors/aliases in draft text are preserved verbatim on save but validated post-resolution by the loader.
