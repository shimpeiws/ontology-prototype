# ontology-prototype
A lightweight FDE operational loop: model business workflows, diagnose with AI, execute human-approved interventions, and measure outcomes.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `packages/core/` | `ontology-core`: workspace contracts, loading, and validation shared by every app |
| `apps/cli/` | `ontology-cli`: the `ontology` command-line app over the shared core |
| `examples/` | Portable workspace definitions and synthetic source data |
| `docs/` | Product plan, release criteria, and architecture decisions |

`apps/api/` and `apps/web/` are documented v0.2 destinations (see [docs/monorepo.md](docs/monorepo.md)); they are intentionally not implemented in v0.1.

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
```

`ontology check` loads a workspace directory (the four files `domain.yaml`, `mapping.yaml`, `evaluation.yaml`, `agent.yaml`), prints structured diagnostics, and exits `0` when the workspace is valid, `1` otherwise. See [docs/workspace.md](docs/workspace.md) for the definition format and validation rules.

Implemented in the v0.1 bootstrap: `ontology check`. Planned capabilities from the [v0.1 acceptance criteria](docs/v0.1.md) — ingestion, projections, agent runs, approval review, write-back, evaluation, and trace — are not commands yet; they will land as later issues on the same shared core.

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
