# Live-model diagnosis comparison report — <YYYY-MM-DD>

> **Template.** Copy this file per executed comparison and fill every
> `<…>` placeholder. A section with nothing to report gets `n/a` plus a
> one-line reason — never delete an empty section, and never fabricate
> evidence a run did not produce.

## Run identity

- Executor: `<name>`
- Date of run: `<YYYY-MM-DD>`
- Command: `ontology diagnose-compare <args actually used>`
- Artifact: `<path to comparison-report.json>` (commit or link it)
- Harness: `eval_version` `<from artifact>`, `prompt_version` `<from artifact>`
- Scratch: `<path>` (the materialized workspace with recorded runs)

## Credential / setup steps taken

<Dated, reproducible steps: where each key came from (e.g.
https://openrouter.ai/keys), which env var carried it
(`OPENROUTER_API_KEY`), what was exported — never the secret values.
Note anything the docs did not cover so they can be updated.>

## Shared input (from the artifact)

- Projection run: `<projection_run_id>`
- Candidates: `<candidate_ids>`
- Input digest: `<input_digest>`
- Identical across all evaluated subjects: `<input_identical — must be true>`

## Subject results

| provider | model | status | repeats | provider requests | latency (s) | checks passed |
| --- | --- | --- | --- | --- | --- | --- |
| `<p>` | `<m>` | `<ok/failed/skipped>` | `<n>` | `<n>` | `<s>` | `<n of m>` |

## Unavailable or failed candidates

| provider | model | recorded reason |
| --- | --- | --- |
| `<p>` | `<m>` | `<reason from artifact.unavailable — or "none">` |

## Quality findings

<Per subject, from the artifact's per-run checks plus a human read of the
recorded runs: were explanations evidence-grounded? facts separated from
labeled hypotheses? follow-up questions useful? interventions plausible?
Cite run ids.>

## Structural validation failures

<Contract violations, rejected proposals, off-list actions, evidence ids
that resolved nowhere — or `none`.>

## Latency, usage, and cost

- Latency: `<per-subject latency_seconds from the artifact>` (wall time
  per subject, including local scoring)
- Usage (tokens in/out) when the provider exposes it: `<recorded values,
  or "not exposed by the provider">`
- Cost: `<estimated or measured>` — price assumptions dated
  `<YYYY-MM-DD>`: `<source and prices used>`
- Absence of usage/cost evidence is itself a finding — record it, do not
  estimate silently.

## Setup tradeoffs

<What it took to get each provider/model running: keys, quotas, gated
models, timeouts, retried runs.>

## Conclusion

<Which subjects produced valid, evidence-linked proposals; implications
for the default-model decision (#33). Confirm that every proposal stayed
non-executable until a human approved the exact payload.>
