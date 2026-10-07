# ADR 0002: Default diagnosis model for v0.1

- Status: Accepted
- Date: 2026-10-06 (opened); decided 2026-10-07
- Decision owner: repository maintainer

## Context

v0.1 needs one default diagnosis configuration, so a fresh install can run
`ontology diagnose` without the operator picking a model first. The default has
to come from a comparison against the implemented task. An agent must not guess
it.

`DEFAULT_MODEL` in `apps/cli/src/ontology_cli/providers.py` held
`openai/gpt-4o-mini` while this record was pending. That value predated any
comparison. One live run against
it succeeded on the job-invoicing example (see [Live run
evidence](../diagnosis.md#live-run-evidence)), which shows the pipeline works end
to end. It does not show that this model is the best of the available
candidates.

The comparison runs on the evaluation harness, which freezes fixtures,
criteria, and a run budget before any live call
(`EVAL_VERSION = "diagnosis-eval.v1"`). Issue #35 builds the runner that
evaluates several provider/model configurations against one shared
`PreparedEval` and writes a comparison report. Issue #36 runs it with real
credentials. Issue #33 owns the decision recorded here.

## Decision

The default is `openrouter` with model id `anthropic/claude-haiku-4.5`.
`DEFAULT_MODEL` holds that id, and [docs/diagnosis.md](../diagnosis.md)
documents it as the default.

The comparison in
[diagnosis-comparison-2026-10-06.md](../diagnosis-comparison-2026-10-06.md)
(issue #36) evaluated three configurations on one shared frozen input. All
three passed all nine checks, so the human rubric decided. It ranked
`anthropic/claude-haiku-4.5` first, `google/gemini-2.5-flash` second, and
`openai/gpt-4o-mini` third. Issue #51 records the outcome here. The v0.1
live run ([v0.1-live-model-run.md](../v0.1-live-model-run.md)) already used
this configuration through an explicit `--model`.

The selection rule below was fixed before #36 ran, so the outcome was
mechanical.

A configuration must pass all nine checks that `score_run` scores:
`contract_valid`, `deliverables_complete`, `permitted_actions_only`,
`proposals_grounded`, `evidence_grounded`, `hypotheses_labeled`,
`intervention_proposed`, `follow_up_offered`, and `run_recorded`. A run that
fails any of them is rejected, whatever its prose looks like. The human rubric
then ranks the configurations that passed, on the plausibility of the
intervention and the usefulness of the follow-up questions. The rubric cannot
rescue a configuration that failed a check.

The budget is the harness default, `RunBudget(repeats=1,
max_provider_requests=8)`. One run per configuration.

The default covers a provider and a model id, `openrouter` plus the winning id.
It is not pinned to a dated snapshot. A dated id would tie the recorded decision
to a version the provider may retire, and the model id is already recorded in
every diagnosis run. The consequence is that the provider can change the weights
behind the id, which is why T2 below exists. The value that ships is
`DEFAULT_MODEL`. The `--model` flag and `$ONTOLOGY_MODEL` override it.

Had no configuration passed the nine checks, this record would have stayed
Pending evidence, and no document would have described the provisional default
as evidence-backed.

## Alternatives

The report records every candidate in one of three states, and the distinction
matters:

- rejected: the configuration ran and was not selected. The reason tag is
  `machine-fail` (a failed check) or `rubric-lost` (passed every check, ranked
  lower).
- unavailable: the configuration could not run, because of a missing key,
  missing credit, or a provider error.
- not run: the configuration was in scope, but the budget did not reach it. The
  reason tag is `budget-exceeded`.

Every entry carries a state, and a reason tag when the state alone does not say
why. Each rejected entry also carries a revisit tag: `revisitable` (a model
update or a price change could change the outcome) or `structural` (the
configuration does not fit this task).

The candidates from the 2026-10-06 report:

| provider | model | state | reason | revisit |
| --- | --- | --- | --- | --- |
| `openrouter` | `anthropic/claude-haiku-4.5` | selected | — | — |
| `openrouter` | `google/gemini-2.5-flash` | rejected | `rubric-lost` | `revisitable` |
| `openrouter` | `openai/gpt-4o-mini` | rejected | `rubric-lost` | `revisitable` |

No candidate was unavailable or not run. Both rejected configurations passed
every check; they lost on the rubric, so a model update or a price change could
change the outcome. `google/gemini-2.5-flash` was the fastest and cheaper per
call than the selected model, which makes it the first candidate to re-check
when T2 or T3 fires.

The raw measurements, latency, provider request counts, and per-check results,
stay in the report at
[diagnosis-comparison-2026-10-06.md](../diagnosis-comparison-2026-10-06.md), so
that the two documents do not carry the same numbers twice.

## Revisit conditions

| id | trigger | how to observe it |
| --- | --- | --- |
| T1 | `EVAL_VERSION` or `PROMPT_VERSION` changes | the constants in `diagnosis_eval.py` and `diagnosis.py`. A bump makes the recorded comparison incomparable |
| T2 | the provider changes the weights behind the model id, or retires it | the same model id on the same `EVAL_VERSION` fails a check that the report recorded as passed |
| T3 | a price change makes the cost comparison wrong | the provider's price page, against the dated prices in the report |
| T4 | a candidate recorded as unavailable becomes available | the unavailable entries in the report |
| T5 | the provider changes the API contract | a `PROVIDER_ERROR` from `ontology diagnose` where the report recorded a success |
| T6 | the diagnosis task changes | `agent.yaml` deliverables or permitted actions change, so the model is no longer judged on the same task |
| T7 | de-identified real data becomes available | a re-run on real snapshots, per the follow-up in [v0.1.md](../v0.1.md) |

Falsifiability. One run on the same `EVAL_VERSION` and `PROMPT_VERSION`, over
identical input, in which another configuration passes all nine checks and ranks
higher on the rubric, overturns this default. Nothing weaker does.

When a trigger fires, open a new issue, re-run the #36 procedure, and set this
record's Status to `Superseded by #NNNN`. Do not reopen #33.

## Consequences

- `DEFAULT_MODEL` is `anthropic/claude-haiku-4.5`, and the documentation no
  longer calls it provisional. `apps/cli/tests/test_adr_default_model.py` fails
  if `docs/diagnosis.md` and `DEFAULT_MODEL` disagree, and fails if the
  provisional wording and this record's Status disagree.
- Changing the default is a change to one constant. Candidate selection,
  canonical state, proposal validation, approval, write-back, and outcome
  evaluation are unaffected, which the `provider-substitution` scenario proves
  by comparing input digests.
- OpenRouter is the only live adapter in v0.1. The replaceable-provider claim
  rests on that substitution proof, not on a second live provider.
- Each comparison report is dated, so a later re-run can be compared with the
  report this decision used.

## Evidence

- Issue #35 built the comparison runner and #36 ran it. Issue #33 was closed
  without recording the outcome; issue #51 records it here.
- Report: [diagnosis-comparison-2026-10-06.md](../diagnosis-comparison-2026-10-06.md),
  committed by #36, with the recorded runs in
  `diagnosis-comparison-2026-10-06-runs.json`.
- Reproduction: `ontology diagnose <workspace> --as-of <time> --provider
  openrouter --model <selected id>`, with `OPENROUTER_API_KEY` set. The
  comparison itself runs through the runner from #35.
