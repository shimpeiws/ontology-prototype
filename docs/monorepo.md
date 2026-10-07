# Monorepo and application boundaries

Status: accepted planning constraint for v0.1. The core/CLI packages and workspace validation are implemented under issue #1; the workspace definition format is documented in [workspace.md](workspace.md).

## Goal

Keep one repository that can host multiple applications over a shared operational core. Establish the core/CLI separation in v0.1 so the FDE workspace UI can be added in v0.2 without moving business logic out of a CLI application.

## Repository layout

| Path | Responsibility | Release |
| --- | --- | --- |
| `packages/core/` | Installable Python package for workspace contracts and the operational loop | v0.1 |
| `apps/cli/` | Installable Python CLI application calling the shared core | v0.1 |
| `apps/api/` | Local FastAPI application exposing shared-core workspace operations ([api.md](api.md)) | v0.2 |
| `apps/web/` | FDE authoring UI — React Router SPA over the API ([web.md](web.md)) | v0.2 |
| `examples/` | Portable workspace definitions and synthetic source data | v0.1 |
| `docs/` | Product plan, release criteria, and architecture decisions | v0.1 |

Each Python component can use its own `pyproject.toml` and `src/` layout. The root documents reproducible installation, validation, and test commands for the repository. Choose minimal tooling that supports local package dependencies; a monorepo does not require a particular build orchestrator.

Create working core and CLI packages in v0.1. Document API and Web destinations now; do not add empty servers, frontend dependencies, or pretend implementations.

## Dependency rules

- CLI and future API depend on the core. The core never imports an app.
- Web calls the API and does not access SQLite or reimplement operational decisions.
- Apps handle transport, argument/request parsing, presentation, and application composition.
- Core owns authoritative workspace validation, mapping semantics, candidate selection, action lifecycle, approval checks, and outcome evaluation.
- Shared workflow operations accept explicit inputs and return typed results or diagnostics without requiring CLI invocation.
- Provider/store/write-back boundaries use narrow interfaces. Apps supply configuration and construct dependencies; runtime integration code does not depend on an app's transport.
- A write-back operation rechecks approval inside the shared execution boundary, so neither CLI nor API can bypass it through an alternate route.
- Validation and metric behavior are defined once. Frontend validation may improve feedback, but backend/core validation remains authoritative.

v0.1 does not implement the entire loop in issue #1. That issue establishes contracts and package boundaries; issues #2–#11 add the remaining capabilities to the same shared core.

## Portable definitions

The four YAML definitions remain domain, mapping, evaluation, and agent task. UI and CLI use equivalent versioned definitions and validation semantics. Future UI saves must produce definitions the CLI can load, and CLI-edited definitions must be usable by the UI.

JSON/API schemas may be derived from shared contracts in v0.2. Avoid independent Python and frontend domain models that can drift. Do not design a generic schema/code-generation platform in v0.1.

## v0.1 acceptance

- Core and CLI are independently identifiable installable packages in the planned layout.
- A clean checkout has documented commands to install both packages and run meaningful checks in CI.
- The core's workspace loader/validator can run from Python without importing CLI code.
- CLI calls that same implementation and produces equivalent validation results and diagnostics.
- Core imports succeed without frontend or API frameworks installed.
- Existing example definitions and CLI entry-point behavior remain usable.
- Business logic added by later v0.1 issues belongs in the shared core, with thin CLI operations.

## v0.2 extension

Add the API and FDE workspace Web app over the established core. Candidate surfaces are domain-model visualization/editing, mapping previews, and evaluation/agent definition editing. Reuse the validation and approval boundaries; choose HTTP/frontend technology and detailed UI flows during v0.2 planning.

Multiple apps do not imply multiple production services, independent databases, multi-user editing, or concurrent writers. Deployment topology and storage coordination require explicit decisions when the API is implemented. Storage coordination between the API and CLI over the shared `.ontology/` store is decided in [ADR 0003](adr/0003-api-operational-boundary.md); deployment topology remains undecided.

## References

- [Product proposal](proposal.md)
- [v0.1 acceptance criteria](v0.1.md)
- [FDE authoring flows and UI states](authoring-flow.md)
- [Foundation implementation issue](https://github.com/shimpeiws/ontology-prototype/issues/1)
