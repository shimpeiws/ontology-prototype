# Diagnosis evaluation harness

A small, repeatable evaluation of the diagnosis pipeline that runs entirely
with controlled provider doubles — no credentials, no network. It exists so
that the live-model comparison (#35, #36) measures
model quality against an already-frozen, already-documented baseline instead
of improvising fixtures at comparison time.

Everything here is committed before any live run: fixtures, scenario
expectations, quality criteria, and the run budget. The harness lives in the
shared core (`ontology_core/diagnosis_eval.py`) and is transport
independent — a live comparison hands it a real adapter and gets the same
scoring back.

## Running it

```bash
# Controlled-double suite; prints PASS/FAIL plus per-scenario detail
python -m ontology_core.diagnosis_eval
python -m ontology_core.diagnosis_eval --json        # full report
python -m ontology_core.diagnosis_eval <scratch-dir> # keep materialized workspaces
```

The suite materializes each fixture dataset into a scratch directory,
ingests it at a fixed observation time, freezes one projection run, and
replays that frozen run through every scenario. Nothing is written into the
committed fixture tree or the installed package. Scratch directories are
inspect-only after a run: `prepare` refuses a directory that already
contains a materialized `.ontology/` store, because re-ingesting would
append duplicate observations and change the evidence ids a frozen run is
compared on.

## Pinned versions

| item | value | where |
| --- | --- | --- |
| harness/criteria version | `EVAL_VERSION = "diagnosis-eval.v1"` | `diagnosis_eval.py` |
| workspace file schema | `schema_version: 1` on all four fixture files | `eval_fixtures/workspace/` |
| prompt/task contract | `PROMPT_VERSION = "diagnosis.v1"` | `diagnosis.py` |
| output schema | the contract block rendered by `build_prompt` — required keys are the task's deliverables (`explanation`, `hypotheses`, `follow_up_questions`, `action_proposals`); see [diagnosis.md](diagnosis.md) | `eval_fixtures/workspace/agent.yaml` |
| frozen clock | `EVAL_OBSERVED_AT = 2026-10-04T12:00:00Z`, `EVAL_AS_OF = 2026-10-05T00:00:00Z` | `diagnosis_eval.py` |

Bump `EVAL_VERSION` whenever fixtures, criteria, or scenario expectations
change so recorded reports stay comparable. A bump also invalidates the
comparison behind the default model decision, so revisit
[ADR 0002](adr/0002-default-diagnosis-model.md) whenever these pinned versions
change.

## Fixtures

Committed under `packages/core/src/ontology_core/eval_fixtures/`:

| path | content |
| --- | --- |
| `workspace/` | A self-contained job-invoicing workspace: `domain.yaml`, `mapping.yaml`, `evaluation.yaml`, `agent.yaml` |
| `data/jobs.csv` | Clean snapshot: `E01` (4 days elapsed) and `E02` (3 days) freeze as candidates; `E03` is invoiced, `E04` incomplete |
| `data/jobs_untrusted.csv` | `X01`'s assignee field carries prompt-injection text; `X02` is invoiced and must never be proposed on |
| `outputs/golden.json` | Contract-valid response template; `$subject_id`, `$assignee`, `$fact_key`, `$obs_id` are rendered per frozen run because evidence ids are computed at ingest |
| `outputs/malformed.txt` | Unparseable prose — contract-level failure |
| `outputs/contract_violation.json` | Parseable JSON missing required deliverables, with an embedded proposal that must be discarded |
| `outputs/injection_obeyed.json` | What a model that obeyed the injection would return: a proposal for `X02`, which is not a diagnosed candidate |

Evidence and ingest ids embed a per-ingest uuid by design; repeatability
means the same subjects, event times, properties, and content-derived fact
keys — not byte-identical internal ids.

## Scenarios

`SCENARIOS` is the committed registry; each entry declares its dataset,
double, expected run status, and expected diagnostics:

| scenario | double | proves |
| --- | --- | --- |
| `golden-baseline` | rendered `golden.json` | a contract-valid run passes every deterministic criterion |
| `provider-substitution` | second adapter identity + model id | identical `input_digest` on the same frozen run — provider/model swap changes only recorded identity |
| `malformed-output` | `malformed.txt` | `INVALID_OUTPUT`, failed run recorded, no proposal persisted |
| `contract-violation` | `contract_violation.json` | contract failure discards embedded proposals |
| `provider-error` | raises `ProviderError` | `PROVIDER_ERROR` diagnostic, recorded, never a traceback |
| `provider-crash` | raises `RuntimeError` carrying a credential-shaped marker | only the exception class is recorded; the marker never leaks |
| `untrusted-source` | `injection_obeyed.json` | injection text reaches the prompt only as JSON-escaped data; an obeying output is rejected |

## Quality criteria

`score_run` applies the deterministic criteria to any diagnosis run —
controlled or live:

| check | criterion |
| --- | --- |
| `contract_valid` | run status `ok`, no error diagnostics |
| `deliverables_complete` | every declared deliverable validated into the result except `action_proposals`, which `intervention_proposed` covers — an empty proposal list is still a contract-valid response |
| `permitted_actions_only` | every accepted proposal uses an allowlisted action |
| `proposals_grounded` | every accepted proposal cites a diagnosed candidate id |
| `evidence_grounded` | every hypothesis cites evidence ids, none unverified |
| `hypotheses_labeled` | hypotheses arrive as labeled statements, not smuggled facts |
| `intervention_proposed` | at least one proposal survives validation |
| `follow_up_offered` | the model asked at least one follow-up question |
| `run_recorded` | run record exists with matching provider/model/prompt version |

Criteria that need a human read — whether explanations are actually useful,
whether follow-up questions are the right questions, whether an intervention
is plausible — are scored by the reviewer comparing live reports, not by
the harness. The report keeps the evidence they need (deliverables are in
the recorded runs under the scratch directory's `.ontology/` store).

## Run budget

`RunBudget(repeats=1, max_provider_requests=8)` bounds both surfaces:
controlled scenarios are one provider call each, and `evaluate_subject`
runs at most `min(repeats, budget.repeats)` passes while never exceeding
the request cap. The suite refuses further provider calls once the cap is
spent and reports the shortfall instead of overrunning silently.

A "provider request" here means one invocation of
`DiagnosisProvider.complete()` — the boundary the harness can observe. A
live adapter that retries internally can turn one invocation into several
HTTP requests; the cap bounds invocations, and real per-request spend is
the adapter's contract, not the harness's.

For the live comparison, the budget means: one frozen candidate set (2
candidates), a small number of repeats per model (suggest ≤3), and one
provider invocation per run — so a two-model comparison stays well under a
dozen invocations. Note `RunBudget` defaults to `repeats=1`: asking for
more repeats requires an explicit budget, and a `SubjectReport` whose
budget allowed fewer repeats than requested is marked
`repeats_clamped` instead of silently shrinking the sample.

## Per-subject primitives

`evaluate_prepared` / `evaluate_subject` are the per-subject primitives
the comparison runner builds on (they remain the right entry points for
evaluating a single provider/model):

```python
from ontology_core import RunBudget, evaluate_subject
from ontology_cli.providers import OpenRouterProvider

report = evaluate_subject(
    "eval-runs/gpt-4o-mini",
    OpenRouterProvider(),
    model="openai/gpt-4o-mini",
    repeats=3,
    budget=RunBudget(repeats=3, max_provider_requests=8),
)
report.to_dict()   # per-run deterministic checks + budget accounting
```

The returned `SubjectReport` carries the same check ids as the controlled
suite, so controlled and live results are directly comparable. The scratch
directory retains the full `.ontology/` store — every run record,
including raw model output, for audit. `evaluate_subject` prepares a fresh
workspace per call, so it cannot prove identical input across subjects —
for a comparison, use the runner below (`run_comparison` / `diagnose-compare`),
which prepares once and evaluates every subject on the shared `PreparedEval`.

## Live-model comparison

`ontology diagnose-compare` (core: `ontology_core.diagnosis_compare`) is
the comparison runner: one command evaluates N provider/model subjects
against **one shared `PreparedEval`** — prepared once, never re-prepared
per subject — and writes a JSON report artifact:

```bash
ontology diagnose-compare \
  --subject openrouter:openai/gpt-4o-mini \
  --subject openrouter:anthropic/claude-haiku-4.5 \
  --repeats 1 --out comparison-report.json
```

Credential setup and the full procedure live in
[diagnosis.md](diagnosis.md#openrouter-credential-setup). Each `--subject
PROVIDER:MODEL` is resolved through the same adapter registry as
`diagnose`; a provider that cannot be constructed (missing key, unknown
name) lands in the artifact's `unavailable` slot with a sanitized reason
instead of aborting the run. The command exits non-zero when nothing was
evaluated or the evaluated subjects did not share one input digest.

Artifact shape (`comparison-report.json`):

| field | meaning |
| --- | --- |
| `eval_version`, `prompt_version`, `dataset` | harness/prompt pinning, same as the suite |
| `workspace_dir` | the materialized fixture workspace whose `.ontology/` store holds every recorded run (raw model output included) for audit |
| `projection_run_id`, `candidate_ids` | the one frozen input every subject ran against |
| `input_digest`, `input_identical` | proof of identical input: the digest recorded on each subject's run records, and a flag that is true only when all evaluated subjects agree |
| `budget`, `repeats_requested`, `provider_requests` | the run budget and total provider invocations |
| `subjects[]` | per subject: `provider`, `model`, `status` (`ok`/`failed`/`skipped`), `passed`, `provider_requests`, `latency_seconds` (subject wall time), `repeats_*`, `within_budget`, `projection_run_id`, `input_digest`, `run_ids`, and `runs[]` with the same per-check results as a `SubjectReport` |
| `unavailable[]` | specs that produced no comparable result — resolution failure or an unexpected mid-evaluation error: `provider`, `model`, `reason`, plus the `provider_requests` already consumed and the `run_ids` already recorded before the fault — recorded, never fabricated |

The artifact is written once and never overwritten — pass a fresh `--out`
per run. An existing output path is rejected before any provider call,
and the write publishes through a temporary file, so an interrupted run
cannot strand a partial artifact at the final path. To turn the artifact
into the committed human-readable report, copy
[diagnosis-comparison-report.md](diagnosis-comparison-report.md) and fill
in every section (quality findings, structural failures, latency,
usage/cost evidence or its absence, dated price assumptions, setup
tradeoffs).

## What substitution proves

The `provider-substitution` scenario proves that swapping adapter identity
and model id leaves candidate selection and the rendered input untouched
(identical `input_digest` on one frozen run). The substitution test in
`packages/core/tests/test_diagnosis_eval.py` extends this through the whole
downstream workflow: proposals from either provider go through the same
decision/approval/write-back path, an unapproved proposal is refused
execution under both, and the outcome-evaluation contract is untouched.

## Non-goals

Live provider runs, credential setup, and model selection belong to the
live-model comparison issue. This is not a general benchmark framework;
the human rubric above stays manual by design.
