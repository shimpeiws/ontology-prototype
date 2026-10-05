# Model-backed diagnosis

`ontology diagnose` sends deterministically selected projection candidates to a
configured AI model, validates the structured output in the core, and records
the run append-only. The model reads evidence and proposes interventions; it
cannot approve or execute them — human review and write-back are later stages.

## Usage

```bash
# Diagnose the candidates of a fresh projection at an explicit as-of
ontology diagnose <workspace> --as-of 2026-10-05T00:00:00Z

# Diagnose the frozen candidates of a previous projection run
ontology diagnose <workspace> --run prj-20261005T000000000000Z-abcd1234

# Inspect a recorded diagnosis run
ontology diagnose <workspace> --inspect dgx-20261005T103533689309Z-27db70b1

# Offline / credential-free: deterministic stub adapter
ontology diagnose <workspace> --as-of 2026-10-05T00:00:00Z --provider stub
```

`--as-of` and `--run` are mutually exclusive candidate sources. `--inspect`
reads a recorded run and combines with neither.

## Provider and model selection

The diagnosis interface (`DiagnosisProvider`) is transport independent: an
adapter receives a rendered prompt plus an explicit model id and returns raw
text. The CLI ships two adapters:

| provider | purpose | credentials |
|---|---|---|
| `openrouter` | Live model via the OpenRouter chat-completions API (stdlib HTTP, no SDK) | `OPENROUTER_API_KEY` env var |
| `stub` | Deterministic local double for tests and credential-free runs | none |

The model is chosen by explicit configuration: `--model`, else
`$ONTOLOGY_MODEL`, else the documented default `openai/gpt-4o-mini`. Swapping
the model or adding a new adapter changes nothing in candidate selection,
canonical state, proposal validation, approval, or write-back.

### OpenRouter credential setup

1. Create an API key at https://openrouter.ai/keys.
2. `export OPENROUTER_API_KEY=sk-or-...` in the shell that runs the CLI.
3. Never commit the key. It is sent only as an `Authorization` header and is
   never written to the workspace, the canonical store, or the trace.

## Output contract

The prompt (`PROMPT_VERSION = "diagnosis.v1"`) carries the agent's goal, the
required deliverables, the permitted action schemas with a concrete example,
and the candidates as JSON data. The model must answer with one JSON object:

```json
{
  "explanation": "...",
  "hypotheses": [{"statement": "...", "evidence": ["fact-...", "obs-..."]}],
  "follow_up_questions": ["..."],
  "action_proposals": [
    {"action": "SendReminder", "params": {"target": "taro", "related_entity": "101", "reason": "..."}}
  ]
}
```

Only the deliverables declared in `agent.yaml` are required. Hypotheses are
labeled as unconfirmed hypotheses by contract. A `ref` param value is the
referenced entity's id as a plain string.

## Validation

The core validates before anything is recorded as a proposal:

- Output must parse as one JSON object (fenced code blocks are unwrapped).
- Every declared deliverable must be present with the right shape.
- Each proposal's `action` must be in `agent.permitted_actions`.
- Params must match the declared schema: required params present, no unknown
  params, scalar types checked.
- A `ref` param must resolve to an existing canonical entity; a ref to the
  projection's subject entity must cite one of the diagnosed candidates.
- An action with no param referencing the projection's subject entity cannot
  produce a proposal — it would be ungrounded in the candidate set.
- Hypothesis evidence ids are checked against the candidate's own evidence
  links and the canonical event log; ids that resolve nowhere are kept but
  flagged `unverified_evidence` with a `DIAGNOSIS.UNVERIFIED_EVIDENCE`
  warning.
- Scalar params are type-checked; `date`/`datetime` params must parse as
  ISO 8601.

Failures split two ways: contract-level failures (unparseable output,
mis-typed deliverables) fail the whole run with `DIAGNOSIS.INVALID_OUTPUT` —
no proposal from that output may reach the validated list; per-item failures
reject only that proposal and are kept in `rejected` with a reason. Rejected
proposals are never executable. A provider failure becomes
`DIAGNOSIS.PROVIDER_ERROR` — never a traceback.

A run caps at 50 candidates (`DIAGNOSIS.TOO_MANY_CANDIDATES`); one provider
call carries the whole candidate set, and an unbounded set would exceed
model output limits and fail opaquely.

## Recording

Every run that reaches provider invocation is appended to `diagnosis_runs`
(append-only, like `projection_runs`): run id, the source projection run id,
provider and model identity, prompt version and full prompt, a digest of the
exact input (candidate set + agent contract), the raw model output,
validated deliverables, accepted and rejected proposals, diagnostics, and a
timestamp. `status` is `ok` or `failed`; `provider_called` is false when no
candidates were in scope. Failures before a run exists (bad arguments, an
unknown or mismatched projection run) emit a `diagnosis.failed` trace event
with a null run id but are not recorded.

Diagnosis never mutates entities, events, or existing projection runs —
but note `--as-of` computes a projection first, which freezes a *new*
`projection_runs` row and emits `projection.*` trace events, exactly as a
standalone `ontology project` would.

Trace events: `diagnosis.completed` / `diagnosis.failed` in
`.ontology/trace.jsonl`.

## Live run evidence

A successful live run against OpenRouter (`openai/gpt-4o-mini`) on the
job-invoicing example produced a validated `SendReminder` proposal for
candidate Job/101 with an explanation, labeled hypotheses citing evidence ids,
and follow-up questions. Earlier iterations that returned `ref` params as
objects were correctly rejected by validation — the validator, not the model,
decides what counts as a proposal. Final provider/model selection and
comparative validation are tracked separately under issue #12.
