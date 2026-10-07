# ontology-prototype
A lightweight FDE operational loop: model business workflows, diagnose with AI, execute human-approved interventions, and measure outcomes.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `packages/core/` | `ontology-core`: workspace contracts, loading, and validation shared by every app |
| `apps/cli/` | `ontology-cli`: the `ontology` command-line app over the shared core |
| `apps/api/` | `ontology-api`: local FastAPI app over the shared core — workspace open/inspect, draft validation, contract metadata |
| `apps/web/` | `ontology-web`: local React Router SPA — the FDE authoring UI over the API |
| `examples/` | Portable workspace definitions and synthetic source data |
| `docs/` | Product plan, release criteria, and architecture decisions |

`apps/api/` and `apps/web/` landed with v0.2 (see [docs/api.md](docs/api.md) and [docs/web.md](docs/web.md)). The framework-neutral authoring-flow and UI-state contract they implement is specified in [docs/authoring-flow.md](docs/authoring-flow.md).

## Documentation

| Document | Content |
| --- | --- |
| [docs/proposal.md](docs/proposal.md) | Product scope and the problem the operational loop solves |
| [docs/v0.1.md](docs/v0.1.md) | v0.1 acceptance criteria and the demonstration workflow |
| [docs/walkthrough.md](docs/walkthrough.md) | Repeatable end-to-end walkthrough of the complete loop |
| [docs/v0.1-live-model-run.md](docs/v0.1-live-model-run.md) | Live-model run evidence for the v0.1 release review |
| [docs/v0.2-walkthrough.md](docs/v0.2-walkthrough.md) | Repeatable FDE authoring walkthrough: UI → YAML → CLI |
| [docs/v0.2.md](docs/v0.2.md) | v0.2 acceptance review, limitations, and single-user model |
| [docs/monorepo.md](docs/monorepo.md) | Repository boundaries and the shared-core rule |
| [docs/workspace.md](docs/workspace.md) | Workspace definition format and validation rules |
| [docs/ingestion.md](docs/ingestion.md) | CSV/JSON ingestion and immutable observation evidence |
| [docs/canonical.md](docs/canonical.md) | Canonical entities, events, and the append-only rules |
| [docs/projections.md](docs/projections.md) | Deterministic projections and frozen runs |
| [docs/diagnosis.md](docs/diagnosis.md) | Model-backed diagnosis, provider adapters, and the default model |
| [docs/diagnosis-evaluation.md](docs/diagnosis-evaluation.md) | Evaluation harness: fixtures, criteria, and run budget |
| [docs/decisions.md](docs/decisions.md) | Proposal review and human approve/reject decisions |
| [docs/execution.md](docs/execution.md) | Approved write-back, receipts, and retry behavior |
| [docs/evaluation.md](docs/evaluation.md) | Outcome evaluation, cohorts, and guardrails |
| [docs/authoring-flow.md](docs/authoring-flow.md) | v0.2 authoring flow and UI state contract |
| [docs/api.md](docs/api.md) | Local API: startup, endpoints, error model, and root enforcement |
| [docs/web.md](docs/web.md) | Local Web app: development workflow, boundaries, and testing notes |
| [docs/adr/](docs/adr/) | Architecture decision records |

## Install

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e "packages/core[test]" -e "apps/cli[test]" -e "apps/api[test]"
```

## Test

```bash
python -m pytest -q
```

## Usage

```bash
ontology check examples/job-invoicing
ontology check examples/job-invoicing --json
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs.csv
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs.csv --json
ontology project examples/job-invoicing --as-of 2026-10-05T00:00:00Z
ontology diagnose examples/job-invoicing --as-of 2026-10-05T00:00:00Z --provider stub
ontology proposals examples/job-invoicing
ontology decide examples/job-invoicing <proposal-id> --approve --reviewer fde-sato
ontology execute examples/job-invoicing <proposal-id>
ontology evaluate examples/job-invoicing --boundary 2026-10-05T00:00:00Z --as-of 2026-10-15T00:00:00Z
ontology trace examples/job-invoicing
ontology trace examples/job-invoicing --subject Job/101
ontology trace examples/job-invoicing --export trace-export.json
ontology diagnose-compare --subject openrouter:openai/gpt-4o-mini --subject openrouter:<model-b> --out comparison-report.json
```

`ontology check` loads a workspace directory (the four files `domain.yaml`, `mapping.yaml`, `evaluation.yaml`, `agent.yaml`), prints structured diagnostics, and exits `0` when the workspace is valid, `1` otherwise. See [docs/workspace.md](docs/workspace.md) for the definition format and validation rules.

`ontology ingest` reads a CSV/JSON snapshot, normalizes every record through one boundary, stores immutable observation evidence under `<workspace>/.ontology/` (git-ignored), and persists accepted entities/events into the canonical SQLite store. Records with missing ids, malformed timestamps, or absent required fields are rejected with per-record diagnostics. Re-ingesting a later snapshot goes through the same path: only new facts append (repeats add observation provenance), a conflicting `occurred_at` keeps both facts under a `CANON.POSSIBLE_CORRECTION` warning, and new facts link to interventions on the same subject as explicit correlation — never causation. See [docs/ingestion.md](docs/ingestion.md) and [docs/canonical.md](docs/canonical.md).

`ontology project` computes a deterministic projection over canonical events at an explicit `--as-of` time and freezes the run for later inspection (`--run <id>`). See [docs/projections.md](docs/projections.md).

`ontology diagnose` hands projection candidates to a replaceable AI provider (live via OpenRouter, or the offline `stub` adapter), validates the structured output in the core — explanation, labeled hypotheses, follow-up questions, permitted action proposals — and records the run append-only for `--inspect`. Validated proposals are persisted for review. See [docs/diagnosis.md](docs/diagnosis.md).

`ontology proposals` lists and inspects persisted proposals; `ontology decide` records an explicit human approve/reject decision bound to the exact payload digest — a changed payload fails the executor-facing approval check and needs a new decision. Approval itself never executes anything. See [docs/decisions.md](docs/decisions.md).

`ontology execute` runs an approved proposal through a write-back adapter after rechecking approval against the stored payload digest. The local `csv` adapter appends one inspectable row to `.ontology/reminders.csv`; every attempt is persisted append-only with an idempotency key, status (`succeeded`/`failed`/`uncertain`), and receipt — repeated success replays the receipt without duplicating, and an uncertain attempt refuses blind retry. See [docs/execution.md](docs/execution.md).

`ontology evaluate` pairs the contract's interval events per canonical subject at an explicit `--as-of`, splits baseline/after cohorts at an explicit `--boundary`, and reports medians, pending/invalid/ambiguous counts, the strict target verdict, and guardrail verdicts from explicit evidence — then freezes the report append-only for `evaluate --run <id>`. Cohorts are time-based observations; the report never claims an intervention caused the change. See [docs/evaluation.md](docs/evaluation.md).

`ontology trace` reassembles everything a run left behind — observation evidence and snapshots, canonical entities and facts, frozen projection/diagnosis/evaluation runs, proposals with decisions and execution attempts, and the per-stage journal — into one linked, read-only view. Follow one subject through the loop with `--subject Job/101`, anchor on any artifact id with `--run`, or write a self-contained export bundle with `--export` (it embeds raw snapshot contents and never overwrites). Observed facts, AI proposals, human decisions, write-backs, and evaluated outcomes carry distinct nature labels, and dangling references surface as `TRACE.UNRESOLVED_LINK` instead of disappearing. See [docs/trace.md](docs/trace.md).

`ontology diagnose-compare` runs the bounded live-model comparison: N `--subject PROVIDER:MODEL` pairs evaluated against one shared frozen fixture input through the same adapters as `diagnose`, producing a JSON report artifact that records identical-input proof (shared `input_digest`/`projection_run_id`), per-subject status/requests/latency/checks, and an explicit `unavailable` slot. See [docs/diagnosis-evaluation.md](docs/diagnosis-evaluation.md#live-model-comparison).

### Local API

The same core is exposed over HTTP for the authoring UI. Start it with an explicit workspace root and open/validate workspaces over `/api/v1`:

```bash
ontology-api --root examples
# POST /api/v1/workspaces/open        {"path": "job-invoicing"}  (paths resolve against the configured roots)
# POST /api/v1/workspaces/drafts/validate  {"path": "...", "files": {...}}
# GET  /api/v1/contract               versioned contract/schema metadata
```

See [docs/api.md](docs/api.md) for startup configuration, the endpoint contract, the error model, and root enforcement.

### Local Web app

The authoring UI runs against the API. With `ontology-api --root examples` running:

```bash
npm --prefix apps/web ci
npm --prefix apps/web run dev   # http://127.0.0.1:5173, /api proxied to the API
```

Open the app, enter `job-invoicing`, and navigate the workspace overview plus the domain/mapping/evaluation/agent-task definitions with draft/active status. See [docs/web.md](docs/web.md) for the full workflow and checks (`npm --prefix apps/web run typecheck|test|build`).

Implemented so far: `ontology check`, `ontology ingest`, `ontology project`, `ontology diagnose`, `ontology proposals`, `ontology decide`, `ontology execute`, `ontology evaluate`, `ontology trace`, `ontology diagnose-compare`.

## Library use

The core is callable directly from Python without any app installed:

```python
from ontology_core import load_workspace

result = load_workspace("examples/job-invoicing")
if not result.ok:
    for d in result.diagnostics:
        print(d.format())
```

The core never imports CLI/API/Web code; apps call `load_workspace` and present the returned diagnostics, so every app accepts and rejects identical configurations.
