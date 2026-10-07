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

export interface ProjectionSpec {
  name: string;
  subject: string;
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
