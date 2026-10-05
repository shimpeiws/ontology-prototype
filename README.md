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
```

`ontology check` loads a workspace directory (the four files `domain.yaml`, `mapping.yaml`, `evaluation.yaml`, `agent.yaml`), prints structured diagnostics, and exits `0` when the workspace is valid, `1` otherwise. See [docs/workspace.md](docs/workspace.md) for the definition format and validation rules.

`ontology ingest` reads a CSV/JSON snapshot, normalizes every record through one boundary, stores immutable observation evidence under `<workspace>/.ontology/` (git-ignored), and persists accepted entities/events into the canonical SQLite store. Records with missing ids, malformed timestamps, or absent required fields are rejected with per-record diagnostics. See [docs/ingestion.md](docs/ingestion.md) and [docs/canonical.md](docs/canonical.md).

`ontology project` computes a deterministic projection over canonical events at an explicit `--as-of` time and freezes the run for later inspection (`--run <id>`). See [docs/projections.md](docs/projections.md).

`ontology diagnose` hands projection candidates to a replaceable AI provider (live via OpenRouter, or the offline `stub` adapter), validates the structured output in the core — explanation, labeled hypotheses, follow-up questions, permitted action proposals — and records the run append-only for `--inspect`. Proposals are validated only; approval and write-back are separate later stages. See [docs/diagnosis.md](docs/diagnosis.md).

Implemented so far: `ontology check`, `ontology ingest`, `ontology project`, `ontology diagnose`. Planned capabilities from the [v0.1 acceptance criteria](docs/v0.1.md) — approval review, write-back, evaluation, and trace inspection — are not commands yet; they will land as later issues on the same shared core.

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
