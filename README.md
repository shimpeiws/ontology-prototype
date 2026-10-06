# ontology-prototype
A lightweight FDE operational loop: model business workflows, diagnose with AI, execute human-approved interventions, and measure outcomes.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `packages/core/` | `ontology-core`: workspace contracts, loading, and validation shared by every app |
| `apps/cli/` | `ontology-cli`: the `ontology` command-line app over the shared core |
| `examples/` | Portable workspace definitions and synthetic source data |
| `docs/` | Product plan, release criteria, and architecture decisions |

`apps/api/` and `apps/web/` are documented v0.2 destinations (see [docs/monorepo.md](docs/monorepo.md)); they are intentionally not implemented in v0.1. The framework-neutral authoring-flow and UI-state contract they implement is specified in [docs/authoring-flow.md](docs/authoring-flow.md).

## Install

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e "packages/core[test]" -e "apps/cli[test]"
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
```

`ontology check` loads a workspace directory (the four files `domain.yaml`, `mapping.yaml`, `evaluation.yaml`, `agent.yaml`), prints structured diagnostics, and exits `0` when the workspace is valid, `1` otherwise. See [docs/workspace.md](docs/workspace.md) for the definition format and validation rules.

`ontology ingest` reads a CSV/JSON snapshot, normalizes every record through one boundary, stores immutable observation evidence under `<workspace>/.ontology/` (git-ignored), and persists accepted entities/events into the canonical SQLite store. Records with missing ids, malformed timestamps, or absent required fields are rejected with per-record diagnostics. Re-ingesting a later snapshot goes through the same path: only new facts append (repeats add observation provenance), a conflicting `occurred_at` keeps both facts under a `CANON.POSSIBLE_CORRECTION` warning, and new facts link to interventions on the same subject as explicit correlation — never causation. See [docs/ingestion.md](docs/ingestion.md) and [docs/canonical.md](docs/canonical.md).

`ontology project` computes a deterministic projection over canonical events at an explicit `--as-of` time and freezes the run for later inspection (`--run <id>`). See [docs/projections.md](docs/projections.md).

`ontology diagnose` hands projection candidates to a replaceable AI provider (live via OpenRouter, or the offline `stub` adapter), validates the structured output in the core — explanation, labeled hypotheses, follow-up questions, permitted action proposals — and records the run append-only for `--inspect`. Validated proposals are persisted for review. See [docs/diagnosis.md](docs/diagnosis.md).

`ontology proposals` lists and inspects persisted proposals; `ontology decide` records an explicit human approve/reject decision bound to the exact payload digest — a changed payload fails the executor-facing approval check and needs a new decision. Approval itself never executes anything. See [docs/decisions.md](docs/decisions.md).

`ontology execute` runs an approved proposal through a write-back adapter after rechecking approval against the stored payload digest. The local `csv` adapter appends one inspectable row to `.ontology/reminders.csv`; every attempt is persisted append-only with an idempotency key, status (`succeeded`/`failed`/`uncertain`), and receipt — repeated success replays the receipt without duplicating, and an uncertain attempt refuses blind retry. See [docs/execution.md](docs/execution.md).

`ontology evaluate` pairs the contract's interval events per canonical subject at an explicit `--as-of`, splits baseline/after cohorts at an explicit `--boundary`, and reports medians, pending/invalid/ambiguous counts, the strict target verdict, and guardrail verdicts from explicit evidence — then freezes the report append-only for `evaluate --run <id>`. Cohorts are time-based observations; the report never claims an intervention caused the change. See [docs/evaluation.md](docs/evaluation.md).

`ontology trace` reassembles everything a run left behind — observation evidence and snapshots, canonical entities and facts, frozen projection/diagnosis/evaluation runs, proposals with decisions and execution attempts, and the per-stage journal — into one linked, read-only view. Follow one subject through the loop with `--subject Job/101`, anchor on any artifact id with `--run`, or write a self-contained export bundle with `--export` (it embeds raw snapshot contents and never overwrites). Observed facts, AI proposals, human decisions, write-backs, and evaluated outcomes carry distinct nature labels, and dangling references surface as `TRACE.UNRESOLVED_LINK` instead of disappearing. See [docs/trace.md](docs/trace.md).

Implemented so far: `ontology check`, `ontology ingest`, `ontology project`, `ontology diagnose`, `ontology proposals`, `ontology decide`, `ontology execute`, `ontology evaluate`, `ontology trace`.

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
