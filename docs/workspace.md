# Workspace definitions and validation

A workspace is a directory containing four YAML definition files. The FDE checks these files in; loading them is data-only — no environment interpolation, no external includes, no Python or SQL execution.

| File | Content |
| --- | --- |
| `domain.yaml` | Typed entity/event model, projections, and explicit policies |
| `mapping.yaml` | Declarative mapping from a source snapshot to entities and events |
| `evaluation.yaml` | Outcome contract: interval, aggregation, baseline, strict target, guardrails |
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
    CompletedButNotInvoiced: { subject: Job }
```

- Property `type` is one of `string`, `integer`, `number`, `boolean`, `date`, `datetime`, `ref`.
- `ref` properties require `ref: <entity name>`; references resolve to entities only (never events or projections).
- Every event and projection requires a `subject` entity.
- Events always carry occurrence and observation times named by `policies.event_times`; schema v1 supports only `date_only_timezone: UTC`.
- `entity_id_type` must be `string` — entity ids are never cast to numbers.

## mapping.yaml

```yaml
mapping:
  source: { name: jobs, format: csv }   # csv | json
  entities:
    - entity: Job
      id: { field: id }                  # id stays a string; 'as' is forbidden here
      properties:
        status:   { field: status }
        assignee: { field: assignee }    # ref target: the raw string id
        amount:   { field: amount, as: number }
  events:
    - event: JobCompleted
      subject: { entity: Job, id: { field: id } }
      when: { op: and, args: [ { op: eq, field: status, value: completed }, { op: not_null, field: completed_at } ] }
      occurred_at: { field: completed_at, as: datetime }
```

- An assignment is `{ field: <column> }` or `{ literal: <scalar> }` — exactly one — plus optional `as` cast.
- `as` must equal the target property's type and is required for non-`string` scalar properties. It is forbidden on `ref` properties and on entity ids.
- `occurred_at` requires `as: date` or `as: datetime`.
- `subject.entity` must equal the event's declared subject in the domain.
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
  guardrails:
    - { name: invoice-error-rate, metric: invoice_error_rate, comparator: not_increase }
```

- `start_event` and `end_event` must be different domain events sharing one subject.
- `target.comparator` is `lt | lte | gt | gte`; `lt` is strict, so a 4-day median misses a `< 4` target.
- Guardrail comparator `not_increase` compares against the baseline and takes no `value`; other comparators require a numeric `value`.

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

Apps call `ontology_core.load_workspace` and present its diagnostics; they do not re-validate. `ontology_core` imports nothing from apps and needs only PyYAML. When `domain.yaml` itself fails, cross-file reference checks are skipped with a `WORKSPACE.CROSS_VALIDATION_SKIPPED` warning so diagnostics stay actionable instead of cascading.
