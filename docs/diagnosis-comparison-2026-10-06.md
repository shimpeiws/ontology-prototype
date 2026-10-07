# Live-model diagnosis comparison report — 2026-10-06

## Run identity

- Executor: `Devin (agent, issue-36 worktree)`
- Date of run: `2026-10-06`
- Command: `ontology diagnose-compare --subject openrouter:openai/gpt-4o-mini --subject openrouter:anthropic/claude-haiku-4.5 --subject openrouter:google/gemini-2.5-flash --repeats 1 --out .verification/diagnosis-comparison-2026-10-06/comparison-report.json .verification/diagnosis-comparison-2026-10-06`
- Artifact: `docs/diagnosis-comparison-2026-10-06.json` (committed copy of
  `comparison-report.json`)
- Recorded outputs: `docs/diagnosis-comparison-2026-10-06-runs.json` —
  the frozen shared input (projection candidates with their evidence
  ids) plus the full recorded run records (deliverables and proposals
  per subject) exported from the comparison store, so the rubric read
  below and every cited `fact-*`/`obs-*` evidence id are auditable from
  the committed artifacts alone
- Harness: `eval_version` `diagnosis-eval.v1`, `prompt_version` `diagnosis.v1`
- Workspace: `.verification/diagnosis-comparison-2026-10-06/shared` (the
  materialized eval workspace; its `.ontology/` store holds every recorded
  run for audit — kept locally, not committed)

## Credential / setup steps taken

- 2026-10-06: The OpenRouter key was already provisioned on the operator
  machine — created earlier at https://openrouter.ai/keys and exported as
  `OPENROUTER_API_KEY` in the shell environment.
  No new key was issued; nothing credential-shaped was written to the
  workspace, the artifact, or this report (verified by grep over the run
  output for the key value and the `sk-or` prefix — zero hits).
- Doc gap found: `docs/diagnosis.md` documents the flags but not how to keep
  the materialized workspace; the scratch directory is a *positional*
  argument (`ontology diagnose-compare [scratch]`) — `--scratch` is not a
  flag and errors. Worth one line in the doc when next touched.

## Shared input (from the artifact)

- Projection run: `prj-20261006T041359783091Z-9d73cf89`
- Candidates: `E01`, `E02`
- Input digest: `sha256:756481a4248da043107bbef4a8a2cf9b5a25c43f1de13eb8d64b2ff359f13ca6`
- Identical across all evaluated subjects: `true`

## Subject results

| provider | model | status | repeats | provider requests | latency (s) | checks passed |
| --- | --- | --- | --- | --- | --- | --- |
| `openrouter` | `openai/gpt-4o-mini` | `ok` | `1` | `1` | `6.064` | `9 of 9` |
| `openrouter` | `anthropic/claude-haiku-4.5` | `ok` | `1` | `1` | `6.1` | `9 of 9` |
| `openrouter` | `google/gemini-2.5-flash` | `ok` | `1` | `1` | `3.921` | `9 of 9` |

## Unavailable or failed candidates

| provider | model | recorded reason |
| --- | --- | --- |
| `n/a` | `n/a` | `none — all three subjects evaluated` |

## Quality findings

All three subjects passed every deterministic check on the same frozen
input. The rubric read of the recorded runs (full outputs in
`docs/diagnosis-comparison-2026-10-06-runs.json`):

- `anthropic/claude-haiku-4.5` (`dgx-20261006T041405876481Z-342fdfb3`) —
  strongest read. The explanation names both jobs with their exact
  elapsed days (4 and 3) and enumerates distinct plausible causes; its
  3 hypotheses cite specific evidence ids per claim (including
  observation ids that verify). Its 5 follow-up questions are the most
  actionable of the set — documented process, pre-existing invoice
  records, assignee responsibility, blocking issues, and the expected
  SLA. Proposals carry per-candidate reasoning ("verify
  billing-readiness") rather than a generic reminder.
- `google/gemini-2.5-flash` (`dgx-20261006T041411989576Z-d34f6fba`) —
  second. Explanation names jobs, days, and assignees; the cleanest
  evidence mapping — each per-job hypothesis cites only its own job's
  fact key, plus one shared hypothesis citing both. 3 relevant
  follow-up questions. Fastest subject (3.921 s).
- `openai/gpt-4o-mini` (`dgx-20261006T041359796907Z-a9c55e43`) — third.
  Explanation names the symptom but omits elapsed days and assignees;
  both hypotheses cite *both* jobs' fact keys indiscriminately
  (grounded but coarse). 2 adequate follow-up questions. Proposals
  target the right subjects with generic reminder text.

## Structural validation failures

`none` — every run was contract-valid: no rejected proposals, no
off-list actions, no unverified evidence ids, no missing deliverables.

## Latency, usage, and cost

- Latency: `gpt-4o-mini` 6.064 s, `claude-haiku-4.5` 6.1 s,
  `gemini-2.5-flash` 3.921 s (wall time per subject, including local
  scoring).
- Usage (tokens in/out) when the provider exposes it: `not recorded —
  the OpenRouter adapter does not capture the usage field from the
  response` (absence is a finding; the provider does return usage in
  the API response, the adapter just drops it).
- Cost: `estimated, clearly labeled` — price assumptions dated
  `2026-10-06`, from `GET https://openrouter.ai/api/v1/models`:
  `gpt-4o-mini` $0.15/M input + $0.60/M output; `claude-haiku-4.5`
  $1.00/M input + $5.00/M output; `gemini-2.5-flash` $0.30/M input +
  $2.50/M output. With an unmeasured ~2–3k-token prompt and ~0.5–1k-token
  completion (usage not captured — see above), each call estimates at
  roughly $0.001, $0.005, and $0.0025 respectively; at the stated upper
  bounds the three calls total ≲ $0.0125, so the whole comparison cost
  under about $0.015.

## Setup tradeoffs

No friction: one pre-existing key, three OpenRouter model ids all
accessible, no quotas hit, no retries needed. The only surprise was the
positional scratch argument (see Credential / setup steps).

## Conclusion

All three configurations produced valid, evidence-linked proposals on
the implemented task — each passes the nine deterministic checks, so
all three are eligible inputs to the default-model decision. On the
human rubric (plausibility of intervention, usefulness of follow-ups)
the ranking is `claude-haiku-4.5` > `gemini-2.5-flash` >
`gpt-4o-mini`, which makes the incumbent provisional default the
weakest of the measured set. Under the mechanical selection rule in
ADR 0002 the evidence points at `anthropic/claude-haiku-4.5`; the
decision itself belongs to #33 and is not made here.

Every proposal stayed non-executable: the comparison never invokes
`decide`/`execute`, and the recorded store holds `0` decision rows and
`0` execution rows across all subjects — an unapproved payload was
never executed.
