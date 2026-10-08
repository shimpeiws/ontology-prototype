/**
 * Client-side types for the v1 API contract (docs/api.md).
 *
 * These describe the transport shapes the API returns — the authoritative
 * definitions live in `ontology_core` and `ontology_api.schemas`. The Web
 * app never re-validates; it renders what the API returns.
 */

/** One core diagnostic, serialized field-for-field by the API. */
export interface Diagnostic {
  severity: string;
  code: string;
  message: string;
  file: string | null;
  path: string | null;
  line: number | null;
  hint: string | null;
}

/** Structured API failure body: `{ error: { code, message } }`. */
export interface ApiErrorBody {
  code: string;
  message: string;
  detail?: unknown;
}

export interface WorkspaceRef {
  path: string;
  /** Null when the on-disk bundle could not be fully represented. */
  digest: string | null;
}

export interface OpenResponse {
  api_version: string;
  valid: boolean;
  workspace: WorkspaceRef;
  files: Record<string, string>;
  definitions: WorkspaceDefinitions | null;
  diagnostics: Diagnostic[];
}

export interface DraftValidateResponse {
  api_version: string;
  valid: boolean;
  digest: string;
  definitions: WorkspaceDefinitions | null;
  diagnostics: Diagnostic[];
}

/** Save outcomes, mirroring ontology_core.save_workspace statuses. */
export type SaveOutcome = "saved" | "invalid" | "conflict" | "failed";

export interface SaveResponse {
  api_version: string;
  outcome: SaveOutcome;
  valid: boolean;
  /**
   * New active digest on `saved`, the current on-disk digest on
   * `conflict` (the base a reloaded client must present), the draft
   * digest on `invalid`, null on `failed`.
   */
  digest: string | null;
  definitions: WorkspaceDefinitions | null;
  diagnostics: Diagnostic[];
  message: string | null;
}

export interface ContractResponse {
  api_version: string;
  core_version: string;
  schema_version: number;
  workspace_files: Record<string, string>;
  diagnostic_fields: string[];
  operations: Record<string, string>;
  contract: Record<string, unknown>;
}

/* ---- Draft mapping preview (POST /workspaces/drafts/preview) ---- */

export interface PreviewRequest {
  path: string;
  files: Record<string, string>;
  /** `source` selects the declared mapping source the sample resolves
   *  through; omitted means the draft's single source. */
  sample: { text: string; source?: string };
}

export interface PreviewEntity {
  entity: string;
  id: string;
  properties: Record<string, unknown>;
}

export interface PreviewEvent {
  event: string;
  subject: { entity: string; id: string };
  occurred_at: string;
  observed_at: string;
}

/** One source record resolved by the draft mapping (core Observation). */
export interface PreviewObservation {
  observation_id: string;
  ingest_id: string;
  source: string;
  row: number;
  status: "accepted" | "rejected" | string;
  observed_at: string;
  content_digest: string;
  mapping_digest: string;
  /** Normalized record: blanks/nulls are null, casts already applied. */
  record: Record<string, string | number | boolean | null>;
  entities: PreviewEntity[];
  events: PreviewEvent[];
  diagnostics: Diagnostic[];
}

export interface PreviewPayload {
  preview_id: string | null;
  /** Null when no source could be selected (see diagnostics). */
  source: string | null;
  format: string | null;
  /** sha256 of the exact sample bytes — the input version. */
  input_digest: string | null;
  /** sha256 of the mapping definition — the mapping version. */
  mapping_digest: string | null;
  observed_at: string | null;
  record_count: number;
  truncated: boolean;
  observations: PreviewObservation[];
  /** Run-level + per-record diagnostics from the shared resolve path. */
  diagnostics: Diagnostic[];
}

export interface DraftPreviewResponse {
  api_version: string;
  valid: boolean;
  /** Digest of the draft bundle the preview ran against. */
  digest: string;
  diagnostics: Diagnostic[];
  /** Null when the draft bundle did not validate. */
  preview: PreviewPayload | null;
}

/* ---- Operational inspection (ADR 0003 read surface, docs/api.md) ----
 *
 * These payloads are the core's own dicts passed through verbatim
 * (`extra="allow"` on the API schemas), so the types describe the fields
 * the views render — additional core fields ride along untyped.
 */

/** One append-only journal event (.ontology/trace.jsonl line). */
export interface TraceJournalEvent {
  ts: string;
  stage: string;
  /** Dotted name such as `observation.accepted` or `diagnosis.failed`. */
  kind: string;
  data: Record<string, unknown>;
}

/** One recorded reference between artifact ids. */
export interface TraceLink {
  from: string;
  to: string;
  via: string;
  resolved: boolean;
}

/** `ontology trace --json` overview sections. */
export interface TraceOverview {
  workspace: string;
  ok: boolean;
  journal: {
    events: number;
    unreadable_lines: number;
    failed_events: number;
  };
  evidence: {
    ingest_runs: string[];
    observations: number;
    snapshots: number;
  };
  canonical: {
    entities: number;
    facts: number;
    fact_links: number;
  };
  projection_runs: {
    run_id: string;
    projection: string;
    as_of: string;
    candidates: number;
  }[];
  diagnosis_runs: {
    run_id: string;
    projection_run_id: string | null;
    provider: string;
    model: string;
    status: string;
  }[];
  proposals: {
    total: number;
    by_status: Record<string, number>;
    unindexed: number;
  };
  decisions: number;
  executions: {
    attempts: number;
    by_status: Record<string, number>;
  };
  evaluation_runs: {
    run_id: string;
    evaluation: string;
    as_of: string;
    overall: string | null;
  }[];
  links: { total: number; unresolved: number };
  diagnostics: Diagnostic[];
}

/** Observation evidence record (same shape as preview observations). */
export interface TraceObservation {
  observation_id: string;
  ingest_id: string;
  source: string;
  row: number;
  status: string;
  observed_at: string;
  content_digest: string;
  mapping_digest: string;
  record: Record<string, string | number | boolean | null>;
  entities: PreviewEntity[];
  events: PreviewEvent[];
  diagnostics: Diagnostic[];
}

/** One canonical fact (event) with its observation provenance. */
export interface TraceFact {
  fact_key: string;
  event: string;
  subject: { entity: string; id: string };
  occurred_at: string;
  observed_at: string;
  first_observation_id: string;
  ingest_id: string;
  observation_ids: string[];
  ingest_ids: string[];
  links: {
    fact_key: string;
    relation: string;
    target: string;
    observation_id: string;
    ingest_id: string;
    context?: Record<string, unknown>;
  }[];
}

export interface TraceSubjectProjection {
  run_id: string;
  projection: string;
  as_of: string;
  created_at: string;
  definition_digest: string;
  /** "candidate" when the run selected this subject, "input" otherwise. */
  role: string;
  evidence: Record<string, unknown> | null;
}

export interface TraceSubjectDiagnosis {
  run_id: string;
  projection_run_id: string | null;
  provider: string;
  model: string;
  /** "ok" or "failed" — a failed run is evidence, not a gap. */
  status: string;
  provider_called: boolean;
  prompt_version: string;
  input_digest: string;
  deliverables: Record<string, unknown>;
  rejected: unknown[];
  proposals: string[];
  created_at: string;
}

export interface TraceDecision {
  decision_id: string;
  proposal_id: string;
  /** "approved" or "rejected". */
  decision: string;
  reviewer: string;
  reason: string | null;
  payload_digest: string;
  decided_at: string;
}

export interface TraceExecution {
  execution_id: string;
  idempotency_key: string;
  proposal_id: string;
  action: string;
  adapter: string;
  attempt: number;
  /** "attempting" | "succeeded" | "failed" | "uncertain". */
  status: string;
  receipt: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
}

export interface TraceProposal {
  proposal_id: string;
  diagnosis_run_id: string;
  proposal_index: number;
  action: string;
  params: Record<string, unknown>;
  reason: string | null;
  subject_entity: string | null;
  subject_id: string | null;
  related_entities?: { entity: string; id: string }[];
  evidence: Record<string, unknown>;
  payload_digest?: string;
  created_at?: string;
  decisions: TraceDecision[];
  executions: TraceExecution[];
  correlated_facts: string[];
  /** proposed | approved | rejected | unindexed. */
  status: string;
  indexed: boolean;
}

export interface TraceSubjectEvaluation {
  run_id: string;
  evaluation: string;
  as_of: string;
  boundary: string;
  contract_digest: string;
  overall: string | null;
  subject: {
    disposition: string | null;
    cohort: string | null;
    detail?: Record<string, unknown>;
  };
}

/** `trace --subject Entity/Id` payload: the linked chain in stage order. */
export interface SubjectTrace {
  subject: { entity: string; id: string };
  entity: Record<string, unknown> | null;
  observations: TraceObservation[];
  facts: TraceFact[];
  ingests: string[];
  snapshots: string[];
  projections: TraceSubjectProjection[];
  diagnoses: TraceSubjectDiagnosis[];
  proposals: TraceProposal[];
  decisions: TraceDecision[];
  executions: TraceExecution[];
  correlated_facts: string[];
  evaluations: TraceSubjectEvaluation[];
  journal: TraceJournalEvent[];
  links: TraceLink[];
  unresolved: TraceLink[];
}

/** `trace --run <id>` payload: one artifact, its links, journal mentions. */
export interface TraceAnchor {
  id: string;
  kind: string;
  nature: string;
  record: Record<string, unknown> | null;
  links: TraceLink[];
  unresolved: TraceLink[];
  journal: TraceJournalEvent[];
  subjects?: string[];
}

/** Base fields of every operational response. */
interface OperationalResponse {
  api_version: string;
  workspace: string;
  /** False on a domain failure; diagnostics carry the reason. */
  ok: boolean;
  diagnostics?: Diagnostic[];
}

export interface TraceOverviewResponse extends OperationalResponse {
  journal?: TraceOverview["journal"];
  evidence?: TraceOverview["evidence"];
  canonical?: TraceOverview["canonical"];
  projection_runs?: TraceOverview["projection_runs"];
  diagnosis_runs?: TraceOverview["diagnosis_runs"];
  proposals?: TraceOverview["proposals"];
  decisions?: number;
  executions?: TraceOverview["executions"];
  evaluation_runs?: TraceOverview["evaluation_runs"];
  links?: TraceOverview["links"];
}

export interface TraceViewResponse extends OperationalResponse {
  /** Omitted on domain failures (unknown id, invalid subject). */
  trace?: SubjectTrace | TraceAnchor;
}

export interface EvaluationRunsResponse extends OperationalResponse {
  evaluation_runs?: EvaluationRunRecord[];
}

export interface EvaluationRunResponse extends OperationalResponse {
  run?: EvaluationRunRecord;
}

/* ---- Frozen evaluation report (ontology_core.outcome) ---- */

export interface CohortPair {
  subject_id: string;
  start_fact_key: string;
  end_fact_key: string;
  started_at: string;
  ended_at: string;
  duration_seconds: number;
  duration: number;
  extra_end_fact_keys?: string[];
  closes_after_boundary?: boolean;
}

export interface CohortPending {
  subject_id: string;
  start_fact_key: string;
  started_at: string;
  elapsed_seconds: number;
  elapsed: number;
}

export interface CohortInvalid {
  subject_id: string;
  reason: string;
  start_fact_key: string;
  end_fact_keys: string[];
}

export interface CohortAmbiguous {
  subject_id: string;
  reason: string;
  start_fact_keys: string[];
  end_fact_keys: string[];
}

export interface EvaluationCohort {
  paired: CohortPair[];
  pending: CohortPending[];
  invalid: CohortInvalid[];
  ambiguous: CohortAmbiguous[];
  counts: { paired: number; pending: number; invalid: number; ambiguous: number };
  /** Number of measured intervals feeding the aggregate. */
  sample: number;
  /** The cohort aggregate (e.g. median duration), null = no evidence. */
  aggregate: number | null;
}

export interface EvaluationChange {
  status: "measured" | "unavailable" | string;
  value?: number;
  /** improvement | regression | unchanged | increase | decrease. */
  direction?: string;
  reason?: string;
}

export interface EvaluationTarget {
  comparator: string;
  value: number;
  /** attained | missed | unverified. */
  status: string;
  reason?: string;
}

export interface EvaluationGuardrail {
  name: string;
  metric: string;
  comparator: string;
  /** passed | failed | unavailable — unavailable is never passed. */
  status: string;
  reason?: string;
  evidence?: Record<string, unknown>;
}

/** Per-subject input row the report examined. */
export interface EvaluationInput {
  subject_id: string;
  start_fact_keys: string[];
  end_fact_keys: string[];
  future_start_fact_keys?: string[];
  future_end_fact_keys?: string[];
  /** paired | pending | invalid | ambiguous | unpaired_end | excluded. */
  disposition?: string;
  cohort?: string;
  excluded?: string;
}

export interface EvaluationReport {
  evaluation: string;
  contract_digest: string;
  as_of: string;
  cohort_boundary: string;
  interval: { start_event: string; end_event: string; subject: string };
  metric: string;
  aggregation: string;
  unit: string;
  declared_baseline: number;
  cohorts: { baseline: EvaluationCohort; after: EvaluationCohort };
  unpaired_ends: { subject_id: string; end_fact_keys: string[] }[];
  change: EvaluationChange;
  target: EvaluationTarget;
  guardrails: EvaluationGuardrail[];
  /** success | not_met | unverified. */
  overall: { status: string };
  inputs: EvaluationInput[];
  /** The report's own no-causation note — always rendered. */
  note: string;
}

export interface EvaluationRunRecord {
  run_id: string;
  evaluation: string;
  as_of: string;
  boundary: string;
  created_at: string;
  contract_digest: string;
  report: EvaluationReport;
}

/* ---- Typed workspace definitions (mirrors ontology_core.model) ---- */

export interface PropertySpec {
  name: string;
  type: string;
  required: boolean;
  ref: string | null;
  description: string | null;
}

export interface EntityType {
  name: string;
  properties: Record<string, PropertySpec>;
  description: string | null;
}

export interface EventType {
  name: string;
  subject: string;
  properties: Record<string, PropertySpec>;
  description: string | null;
}

export interface ElapsedSpec {
  event: string;
  comparator: string;
  value: number;
  unit: string;
}

export interface SelectionSpec {
  required_events: string[];
  absent_events: string[];
  elapsed: ElapsedSpec | null;
  where: { raw: Record<string, unknown> } | null;
}

export interface ProjectionSpec {
  name: string;
  subject: string;
  select: SelectionSpec;
  description: string | null;
}

export interface DomainDefinition {
  name: string;
  description: string | null;
  policies: {
    entity_id_type: string;
    event_times: {
      occurrence_field: string;
      observation_field: string;
      date_only_timezone: string;
    };
  };
  entities: Record<string, EntityType>;
  events: Record<string, EventType>;
  projections: Record<string, ProjectionSpec>;
}

export interface FieldAssignment {
  field: string | null;
  literal: string | number | boolean | null;
  cast: string | null;
}

export interface EntityBinding {
  entity: string;
  id: FieldAssignment;
  properties: Record<string, FieldAssignment>;
}

export interface EventBinding {
  event: string;
  subject: { entity: string; id: FieldAssignment };
  when: { raw: Record<string, unknown> } | null;
  occurred_at: FieldAssignment;
  observed_at: FieldAssignment | null;
}

export interface SourceMapping {
  name: string;
  format: string;
  entities: EntityBinding[];
  events: EventBinding[];
}

export interface MappingDefinition {
  name: string;
  description: string | null;
  sources: SourceMapping[];
}

export interface EvaluationDefinition {
  name: string;
  description: string | null;
  interval: { start_event: string; end_event: string };
  metric: string;
  aggregation: string;
  unit: string;
  baseline: number;
  target: { comparator: string; value: number };
  guardrails: {
    name: string;
    metric: string;
    comparator: string;
    value: number | null;
    description: string | null;
  }[];
}

export interface AgentDefinition {
  name: string;
  description: string | null;
  projection: string;
  task: { goal: string; deliverables: string[] };
  permitted_actions: Record<
    string,
    { name: string; params: Record<string, PropertySpec>; description: string | null }
  >;
}

export interface WorkspaceDefinitions {
  domain: DomainDefinition;
  mapping: MappingDefinition;
  evaluation: EvaluationDefinition;
  agent: AgentDefinition;
}

/** The four workspace definition file names, in canonical order. */
export const WORKSPACE_FILE_NAMES = [
  "domain.yaml",
  "mapping.yaml",
  "evaluation.yaml",
  "agent.yaml",
] as const;

export type WorkspaceFileName = (typeof WORKSPACE_FILE_NAMES)[number];
