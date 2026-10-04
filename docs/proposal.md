# FDE Operational Loop Prototype Proposal

This document defines the product intent, operational loop, architecture, scope, and longer-term direction. See [v0.1 acceptance criteria](v0.1.md) for the first release's definition of completion. These are planned capabilities, not a claim that the loop is already implemented.

## 1. Overview

Implement the smallest software system in which an FDE can enter an unfamiliar business, understand its workflow, build a minimum model, execute an improvement hypothesis with an AI agent, and measure the business outcome.

The project is generic and intended for open source. It is not a scaled-down knowledge platform. It tests which primitives are genuinely required to improve one real operational workflow quickly.

## 2. Problem

An FDE must understand the business and workflow, identify constraints and relevant operational data, build a model, diagnose the problem, implement an intervention, and measure impact.

Business understanding, data investigation, integration, metric definitions, agent implementation, and evaluation are usually assembled separately for each business. Knowledge and implementation are therefore difficult to reuse.

## 3. Hypothesis

The common loop is: Business → Outcome → Workflow → Evidence → Operational Model → Diagnosis → Intervention → Evaluation.

A minimal common foundation may increase the speed of understanding an unfamiliar company, building a prototype, connecting AI to operations safely, and verifying improvements.

## 4. Core principle

**Start from the business, not from the data.**

Ask what the business does, how value flows, where work gets stuck, and which outcome should improve. Then work backward to the evidence necessary to observe that outcome.

## 5. Primary user

The primary user is the FDE. Building a new frontline operational UI is not the purpose. Teams continue using Excel, CSV, SaaS, calendars, email, chat, and internal tools. The prototype adds business understanding and agent intelligence behind those tools.

## 6. Target FDE workflow

1. Understand the business.
2. Define an evaluation contract.
3. Model the workflow.
4. Define the minimum domain model.
5. Connect required sources.
6. Create canonical operational state.
7. Run AI-assisted diagnosis.
8. Propose an intervention.
9. Have a human approve or reject the action.
10. Execute write-back.
11. Observe the outcome.
12. Compare against the baseline.

## 7. Example use case

A generic job-management workflow uses `jobs.csv` with `id,status,assignee,completed_at,invoiced_at`. Job 101 is completed, assigned to taro, completed on 2026-10-01, and has no invoice timestamp. Job 102 remains in progress, assigned to hanako.

The business interview reveals that completed jobs take too long to reach invoicing.

## 8. Evaluation contract

Define the outcome before modeling. Primary outcome: completion-to-invoice latency. Baseline median: 9 days. Target median: **less than 4 days**. Guardrail: invoice-error rate must not increase. The contract determines which events and data are required.

## 9. Minimum domain model

Define only the necessary concepts: Job, Invoice, Person; JobCompleted and InvoiceIssued; Job assigned to Person and Invoice belonging to Job. Do not begin with a complete ontology.

## 10. Semantic mapping

Map physical fields to business meaning: `id` to Job.id, `status` to Job.status, `assignee` to Job.assignee, `completed_at` to JobCompleted.occurred_at, and `invoiced_at` to InvoiceIssued.occurred_at.

Semantic mapping is the layer that converts existing operational data into canonical business concepts.

## 11. Operational model

Use an entity store, an event store, and projections.

## 12. Entity store

Keep current state, such as Job 101 with status completed and assignee taro. A simple SQLite store is sufficient.

## 13. Event store

Keep business events append-only. A JobCompleted event refers to Job/101, occurred on 2026-10-01, was observed on 2026-10-02, and came from jobs.csv. Preserve temporal business facts and distinguish occurrence from observation.

## 14. Projection

Produce read models for the FDE and agent. `CompletedButNotInvoiced` includes Job/101, completion true, invoice issuance false, and days since completion 3.

## 15. AI agent

The agent reads the operational model. Deterministic logic selects candidates where completion is true, invoice issuance is false, and days since completion is greater than 2. The agent explains the situation, generates cause hypotheses, identifies additional information to check, and suggests interventions.

## 16. Example agent output

Job 101 has been completed for three days without an invoice. Possible causes include completion evidence not reaching the office, invoice preparation awaiting manual confirmation, or additional work remaining. A possible intervention is a reminder to confirm billing readiness. These are hypotheses, not established facts.

## 17. Action proposal

Agents propose explicit actions and do not operate external systems directly. Example: SendReminder, target Person/taro, related entity Job/101, reason completed job without invoice, status proposed.

## 18. Human approval

Every action requires human approval: agent proposal → FDE review → approve or reject. Advanced roles and risk policies are outside v0.1.

## 19. Write-back

Approved actions pass through a simple adapter. Output CSV, mock API, webhook, or email stub is sufficient. Example: SendReminder → mock notification adapter → reminders.csv.

## 20. Re-observation

Continue observing external sources after execution. A later invoiced_at value of 2026-10-05 creates InvoiceIssued, closing the loop.

## 21. Evaluation

Measure against the contract. A change from median 9 days to median 4 days is an improvement; it does not achieve the strict target of less than 4 days. Evaluate the **business outcome**, not agent accuracy. Report guardrails separately.

## 22. End-to-end loop

Business interview → evaluation contract → domain model → semantic mapping → source ingestion → canonical entities → canonical events → projection → AI agent → action proposal → human approval → write-back → re-observation → outcome evaluation.

One complete workflow is the v0.1 success condition.

## 23. Architecture

Use five layers: FDE workspace; agent runtime; operational model; mapping and integration; external operational data. Keep responsibilities small and explicit.

## 24. FDE workspace

A CLI plus configuration files is sufficient. The FDE defines four things: domain model, semantic mapping, evaluation contract, and agent task.

## 25. Domain model definition

Use a typed YAML schema for entities and events, for example Job.status as string, Job.assignee as a Person reference, Person.name as string, and JobCompleted/InvoiceIssued with a Job subject. No advanced ontology engine is required.

## 26. Mapping definition

Use declarative mappings for source fields, entity IDs, properties, event conditions, and occurrence timestamps. The original conceptual examples use JSON-path-like fields and comparisons; implementation may use a restricted structured condition format rather than a general expression language.

## 27. Evaluation definition

An evaluation names its start event, end event, aggregation, baseline, and target. For completion-to-invoice latency, use JobCompleted, InvoiceIssued, median, baseline 9 days, and target less than 4 days.

## 28. Agent definition

Specify the projection to inspect, the problem to reason about, and the actions permitted as proposals. Example: inspect CompletedButNotInvoiced, identify likely causes, and propose SendReminder.

## 29. Traceability

Although advanced identity resolution and governance are excluded, retain the observed data, projection, agent conclusion, proposed action, human decision, execution result, and later outcome.

## 30. Minimum trace example

A run links observed Job/101, the CompletedButNotInvoiced projection, a billing-delay hypothesis, a SendReminder proposal, a human approval, successful execution, a later InvoiceIssued event, and latency of 4 days. Link each stage so the FDE can inspect the evidence.

## 31. v0.1 scope

Include typed entities and events, CSV and JSON ingestion, semantic mapping, canonical entity state, append-only events, simple projections, evaluation contracts, AI-agent runtime, action proposals, human approval, simple write-back, trace, and outcome evaluation.

## 32. Out of scope

Exclude multi-company identity resolution, portfolio ontologies, ontology promotion, complex relationship inference, production connector ecosystems, CDC infrastructure, Saga orchestration, complex authorization, RBAC, automated risk classification, autonomous high-risk actions, graph databases, RDF/OWL, general workflow engines, full BI dashboards, and agent-swarm orchestration.

## 33. AI versus deterministic logic

Deterministic logic owns mapping, event generation, metric calculation, threshold detection, projections, and evaluation. AI owns interpretation, hypotheses, explanations, intervention suggestions, and reasoning about unstructured context.

**Deterministic where possible; AI where judgment is useful.**

## 34. Why this is an OSS project

The core contains no company-specific concepts. It provides generic Job, Invoice, Person, Event, Action, and Evaluation primitives. Industry domain models are defined by users. The framework converts operational reality into a typed business model and closes diagnosis, governed intervention, and outcome evaluation.

## 35. What this project is not

It is not an ERP, data warehouse, BI platform, general agent framework, ontology editor, workflow engine, or replacement for existing operational systems. It adds business understanding and operational intelligence above them.

## 36. Prototype success criteria

In one workflow, an FDE can define an outcome and minimum model; map real operational data; build canonical entities and events; identify a condition through a projection; run AI inspection; obtain an action proposal; approve it; execute write-back; observe the result; and compare the business outcome with baseline.

Synthetic fixtures make the demonstration reproducible. A separate local run with a de-identified real operational sample can verify applicability without committing customer data; it is a recommended follow-up when a suitable sample is available.

## 37. One-sentence definition

A lightweight framework that lets an FDE model one operational workflow, connect real business data, run an AI-assisted diagnosis, execute a human-approved intervention, and measure whether the business outcome improved.

## 38. Longer-term direction

Add only capabilities justified by actual use. Possible future work includes entity resolution, provenance/confidence, governance policy, reconciliation/Saga, domain-ontology reuse, cross-company knowledge, evaluation patterns, reusable interventions, and automatic model suggestions. Build the first complete loop before choosing those abstractions.
