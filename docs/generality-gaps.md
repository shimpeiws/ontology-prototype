# Generality gap log

Where the second workflow (`examples/ticket-sla`, recorded in
[ticket-sla-walkthrough.md](ticket-sla-walkthrough.md)) could not be
expressed in YAML alone. Each gap is mapped to the issue that scopes its
fix, or explicitly accepted for v0.3.

| ID | Gap | Where YAML was insufficient | Mapped to |
| --- | --- | --- | --- |
| G-01 | Candidate selection is code-registered | `domain.yaml` names the projection (`OpenedWithoutFirstResponse`), but its semantics live in `projections.py::_SEMANTICS` keyed by the job-invoicing name `CompletedButNotInvoiced`. `ontology project`/`diagnose` fail with `PROJECTION.UNSUPPORTED_PROJECTION`. The one registered semantic also hardcodes a 2-day threshold (`DELAY_THRESHOLD`), so even a registered name could not express this workflow's 4-hour SLA — selection needs as-of-relative duration comparisons declared in the definition. | #55 |
| G-02 | Guardrail metrics are code-registered | `evaluation.yaml` declares `reopen_rate` with comparator `not_increase`, but `_GUARDRAIL_METRICS` in `outcome.py` registers only `invoice_error_rate`. `ontology evaluate` reports the guardrail `unavailable` and `overall: unverified`. The metric's needed shape — numerator/denominator over `Resolution` entities related to cohort `Ticket` subjects via a declared `ticket` ref, with a `reopened` boolean predicate — matches the declarative guardrail scope. | #56 |
| G-03 | One mapping, one source | Reopen evidence lives in a resolution export (`data/resolutions-*.csv`), not the tickets export. `mapping.yaml` accepts a single `source` mapping; a `sources:` list is rejected as an unknown key, so `Resolution` is declared in `domain.yaml` but cannot be bound or ingested. | #57 |
| G-04 | Delivery adapters are keyed on the action name | `CsvReminderAdapter.deliver` refuses any action other than the literal `SendReminder`, so a workflow-specific action name (e.g. an escalation) could be declared in `agent.yaml` but never delivered. Accepted for v0.3: `ticket-sla` reuses `SendReminder` (`target: Agent`, `related_entity: Ticket`), which is generic enough to carry the reminder intervention. No issue filed; revisit if a third workflow needs a distinct action. | accepted |
| G-05 | Web UI cannot create projections or workspaces | The structured domain editor can rename/remove/re-subject an existing projection but not create one, and no UI flow creates a workspace — the four definitions were authored as YAML text (this is the known unassigned authoring gap in [authoring-flow.md](authoring-flow.md), not a regression). Accepted for v0.3: raw-YAML authoring round-trips through the UI. | accepted |
| G-06 | `FirstResponse` is pre-computed in the export | A realistic ticket source exports replies, and first response is the earliest public reply per ticket; the snapshots instead carry a `first_response_at` column. Earliest-per-subject event selection is not expressible in the condition language. Accepted for v0.3: the synthetic export pre-computes the field; revisit if #57's multi-source work needs event derivation. | accepted |

## Coverage check

`ontology check examples/ticket-sla` passes with 0 errors and 0
warnings, so every gap above is a *runtime or authoring* gap — the
schema validates the intent, the code registry or single-source mapping
refuses it. `apps/cli/tests/test_ticket_sla_cli.py` pins the runnable
surface and each `unavailable`/`UNSUPPORTED` outcome above.
