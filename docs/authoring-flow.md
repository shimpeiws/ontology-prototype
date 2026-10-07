# FDE authoring flows and UI states

Framework-neutral interaction contract for the v0.2 FDE workspace UI. This document specifies *what* the authoring experience must do; it deliberately makes no API/frontend framework or provider/model choice — that decision is tracked separately in issue #23 and recorded before framework-dependent work in #14/#15 proceeds.

Terminology follows the existing contracts: a *workspace* is a directory of four YAML definitions (`domain.yaml`, `mapping.yaml`, `evaluation.yaml`, `agent.yaml`) validated by the shared core ([workspace.md](workspace.md)). *Diagnostics* are the structured results the core returns (severity, stable code, file, dotted path, hint). *Active bundle* means the four files on disk the CLI would load; *draft bundle* means uncommitted edits held by the UI.

## Boundaries

The authoring flow spans four surfaces with fixed responsibilities ([monorepo.md](monorepo.md)):

| Surface | Owns | Must never |
| --- | --- | --- |
| Web (`apps/web`) | Presentation, draft state, field-level feedback, navigation | Access SQLite, reimplement authoritative validation, persist provider credentials in committed definitions or browser storage |
| API (`apps/api`) | Typed/versioned request/result contract over the core; workspace load/inspect, draft validation, isolated preview, save; enforce configured workspace roots | Execute CLI subprocesses for business operations, duplicate core validation |
| Shared core (`packages/core`) | Authoritative validation, mapping semantics, workspace persistence, bundle digests | Import any app |
| CLI (`apps/cli`) | `ontology check`/`ingest`/`project` over the same core and the same YAML files | Diverge from the core's diagnostics or file format |

Draft edits live between the Web draft state and the API's draft-validation/preview operations until an explicit save writes them through core persistence to the YAML files — the same files the CLI reads, in both directions.

## Authoring flow

One coherent flow, matching the v0.1 loop order (outcome-aware, source-informed):

1. **Open workspace.** The FDE selects a workspace directory inside the configured roots. The API loads the four files and returns the bundle plus all diagnostics.
2. **Review workspace overview.** The overview shows bundle status (valid/invalid, active digest) and links into each definition editor.
3. **Inspect/edit the domain.** Entities, typed properties, references, event subjects, and projections are shown as a compact visual model with an equivalent list; structured forms edit supported definitions.
4. **Edit the mapping and preview.** The FDE binds source fields to entities/properties and writes event conditions in the restricted condition language, then runs an isolated preview against a bounded source sample to see canonical entities/events with row/field diagnostics.
5. **Edit the evaluation contract.** Interval start/end events, aggregation, baseline, strict target, and guardrails, with reference validation against the domain.
6. **Edit the agent task.** Projection input, goal, deliverables, and permitted action schemas — without coupling to any provider/model and without granting approval or write-back authority.
7. **Validate the draft bundle.** Cross-definition diagnostics are returned for the whole draft and surfaced per field; editing any definition re-enters draft state.
8. **Save explicitly.** The FDE reviews the pending save; the API validates the complete bundle, checks the base digest against on-disk files, and activates atomically — or reports validation, conflict, or persistence failure without touching the active bundle.
9. **Verify downstream.** Saved YAML is loadable by `ontology check` and usable by the operational loop; the end-to-end walkthrough (#20) proves this.

Steps 3–6 are navigable in any order after step 2; the linear path is the expected first-time flow.

## Views and primary operations

| View | Primary operation | Implemented by |
| --- | --- | --- |
| Workspace open | Select/open a workspace under configured roots; show load result | #14, #15 |
| Workspace overview | Bundle status, active-vs-draft summary, navigate to editors | #15 |
| Domain editor | Inspect the model; add/edit/remove entities, properties, references, events | #16 |
| Mapping editor | Edit source/entity/property/event bindings; run isolated preview | #17 |
| Evaluation editor | Edit interval, aggregation, baseline, target, guardrails | #18 |
| Agent task editor | Edit projection input, goal, deliverables, permitted actions | #18 |
| Validation surface | Bundle diagnostics list; jump to the owning field | #14, #19 |
| Save review | Validate, digest-check, activate, or report conflict/failure | #19 |
| End-to-end walkthrough | Repeatable script covering the whole flow | #20 |

## UI states

Every view renders one state at a time; states are shared patterns (#15), not per-view inventions.

- **Loading.** Workspace open, draft validation, preview, and save are async operations with a distinct in-progress state; stale results are never shown as current — a draft change invalidates displayed preview and validation output.
- **Empty.** A valid but unpopulated definition (no entities, no mapping source selected, preview not yet run) shows an actionable empty state, not an error.
- **Error.** Missing/inaccessible workspaces, malformed requests, API failures, and failed saves produce diagnostic-bearing error states with retry where the operation is retryable; transport errors never masquerade as validation findings.
- **Validation.** Core diagnostics appear as a cross-definition list (severity, code, file, dotted path, hint) and as markers on the owning field; errors block activation, warnings do not.
- **Unsaved draft.** Each definition and the bundle as a whole expose a draft/active indicator; navigating away or closing with unsaved changes warns the FDE. Discarding is explicit: "revert to active" for one file or the whole bundle asks for confirmation, restores the byte-exact active text, and is unavailable while a save is in flight.
- **Active definition.** The baseline is the on-disk bundle at its loaded digest; the FDE can always compare draft against active, and save only proceeds against that digest.

## Field-level feedback and navigation

- Diagnostics are addressable: file + dotted path lets the UI place a marker on the exact field (e.g., `mapping.yaml` → `mapping.entities[0].properties.assignee`) and cross-file references navigate to the referenced definition (e.g., an `assignee` `ref` to `Person` opens the domain editor on `Person`; an evaluation `start_event` opens the domain event).
- Renaming or removing a referenced concept shows its blast radius across the draft bundle before the save boundary (#16, #19).
- The Web layer may add convenience validation (required-field presence, type hints) for feedback, but only core diagnostics decide whether a draft is saveable.

## Save contract

Save is one shared flow for all four editors (#19):

1. Explicit trigger — never implicit autosave-to-active.
2. Whole-bundle validation first; an invalid draft cannot activate.
3. Base digest check — a workspace changed outside the UI (e.g., by the CLI) since load produces a conflict state with an explicit reload/review path, never a silent overwrite.
4. Atomic activation — failed persistence leaves the previous active bundle intact, never a mixed bundle.
5. Result feedback — success, validation-blocked, conflict, or retryable failure is shown distinctly.

Saving definitions mutates only the four YAML files: it never touches canonical entities/events, observations, traces, proposals, or approvals, and the authoring UI holds no path to approve proposals or execute write-backs. Operational governance — inspecting canonical state and recording approve/reject decisions — is a separate console surface governed by [ADR 0003](adr/0003-api-operational-boundary.md), not part of the authoring flow.

## Coverage map

| Flow requirement | Implementing issue |
| --- | --- |
| Workspace load/inspect, draft validation API, configured roots, typed contract | #14 |
| Web shell, navigation, shared form/error/loading/empty patterns, draft/active status, accessibility | #15 |
| Domain visualization, structured editing, reference impact | #16 |
| Mapping forms, restricted condition editing, isolated preview with evidence and invalidation | #17 |
| Evaluation and agent-task editing, credential exclusion, cross-definition checks | #18 |
| Core persistence, explicit save, digest conflict detection, atomic activation, YAML round-trip | #19 |
| Draft discard: confirmed per-file and whole-bundle revert to the active bundle | #53 |
| End-to-end verification and documented walkthrough | #20 |

## Gaps and unassigned requirements

Called out, not silently invented:

- **Workspace creation.** No issue covers creating a new, initially empty workspace from the UI; the flow assumes opening an existing workspace (e.g., the example). If desired, this is new scope.
- **Workspace listing/switching.** #14 requires configured roots but no issue defines enumerating or switching between multiple workspaces.
- **Projection definition editing.** Domain `projections:` are referenced by #16 (visualization) and consumed by #18 (agent input), but no issue explicitly assigns a dedicated projection-editing control; per #18 the bundle must round-trip fields the UI does not expose.
- **Draft restore after restart.** Whether an unsaved draft survives a browser restart is unspecified by #14–#20.

These gaps are documentation of uncovered requirements, not commitments.

## Out of scope

Implementing the API or Web UI, choosing the stack (#23), changing the four-definition contract, multi-user/collaborative editing, action execution or approval UI, and end-to-end acceptance evidence (#20).

## References

- [Workspace definitions and validation](workspace.md)
- [Monorepo and application boundaries](monorepo.md)
- [Product proposal](proposal.md)
- [v0.1 acceptance criteria](v0.1.md)
- Issues #14–#20 (v0.2 implementation), #23 (stack decision)
