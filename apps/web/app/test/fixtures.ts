/**
 * SYNTHETIC FIXTURE — not real API output.
 *
 * These objects are hand-constructed to match the documented `/api/v1`
 * contract (docs/api.md) and the job-invoicing example workspace shape.
 * They exist only for jsdom/unit tests; production code paths always call
 * the real API. If the contract changes, update these fixtures AND verify
 * against a live `ontology-api --root examples` once.
 */

import type {
  ContractResponse,
  Diagnostic,
  DraftValidateResponse,
  OpenResponse,
  SaveResponse,
} from "../lib/types";

export const SYNTHETIC_FILES: Record<string, string> = {
  "domain.yaml":
    "schema_version: 1\nkind: domain\ndomain:\n  name: job-invoicing\n",
  "mapping.yaml":
    "schema_version: 1\nkind: mapping\nmapping:\n  name: jobs-csv\n",
  "evaluation.yaml":
    "schema_version: 1\nkind: evaluation\nevaluation:\n  name: completion-to-invoice-latency\n",
  "agent.yaml":
    "schema_version: 1\nkind: agent\nagent:\n  name: billing-delay-diagnosis\n",
};

export const SYNTHETIC_DIAGNOSTIC: Diagnostic = {
  severity: "error",
  code: "WORKSPACE.EXAMPLE",
  message: "Synthetic diagnostic for tests only.",
  file: "domain.yaml",
  path: "domain.entities.Job",
  line: 12,
  hint: "Synthetic hint.",
};

export const SYNTHETIC_OPEN_RESPONSE: OpenResponse = {
  api_version: "v1",
  valid: true,
  workspace: {
    path: "/abs/example/job-invoicing",
    digest: "synthetic-digest",
  },
  files: { ...SYNTHETIC_FILES },
  definitions: {
    domain: {
      name: "job-invoicing",
      description: "Synthetic domain.",
      policies: {
        entity_id_type: "string",
        event_times: {
          occurrence_field: "occurred_at",
          observation_field: "observed_at",
          date_only_timezone: "UTC",
        },
      },
      entities: {
        Job: {
          name: "Job",
          description: "A unit of work.",
          properties: {
            status: {
              name: "status",
              type: "string",
              required: true,
              ref: null,
              description: null,
            },
          },
        },
      },
      events: {
        JobCompleted: {
          name: "JobCompleted",
          subject: "Job",
          properties: {},
          description: null,
        },
      },
      projections: {},
    },
    mapping: {
      name: "jobs-csv",
      description: null,
      source: { name: "jobs", format: "csv" },
      entities: [
        {
          entity: "Job",
          id: { field: "id", literal: null, cast: null },
          properties: {
            status: { field: "status", literal: null, cast: null },
          },
        },
      ],
      events: [],
    },
    evaluation: {
      name: "completion-to-invoice-latency",
      description: null,
      interval: {
        start_event: "JobCompleted",
        end_event: "InvoiceIssued",
      },
      metric: "duration",
      aggregation: "median",
      unit: "days",
      baseline: 9,
      target: { comparator: "lt", value: 4 },
      guardrails: [],
    },
    agent: {
      name: "billing-delay-diagnosis",
      description: null,
      projection: "CompletedButNotInvoiced",
      task: {
        goal: "Synthetic goal.",
        deliverables: ["explanation"],
      },
      permitted_actions: {},
    },
  },
  diagnostics: [],
};

export const SYNTHETIC_VALIDATE_RESPONSE: DraftValidateResponse = {
  api_version: "v1",
  valid: false,
  digest: "synthetic-draft-digest",
  definitions: null,
  diagnostics: [SYNTHETIC_DIAGNOSTIC],
};

export const SYNTHETIC_SAVE_RESPONSE: SaveResponse = {
  api_version: "v1",
  outcome: "saved",
  valid: true,
  digest: "synthetic-saved-digest",
  definitions: null,
  diagnostics: [],
  message: null,
};

/**
 * SYNTHETIC FIXTURE — a small but complete draft bundle shaped after
 * examples/job-invoicing, used by the domain-editor tests. Includes a
 * comment so round-trip fidelity is exercised.
 */
export const SYNTHETIC_DOMAIN_YAML = `# edited in tests
schema_version: 1
kind: domain
domain:
  name: job-invoicing
  description: Minimal model.
  policies:
    entity_id_type: string
    event_times:
      occurrence_field: occurred_at
      observation_field: observed_at
      date_only_timezone: UTC
  entities:
    Job:
      description: A unit of work.
      properties:
        status: { type: string, required: true }
        assignee:
          type: ref
          ref: Person
    Person:
      properties:
        name: { type: string }
  events:
    JobCompleted: { subject: Job }
    InvoiceIssued:
      subject: Job
      properties:
        invoice: { type: ref, ref: Invoice }
  projections:
    CompletedButNotInvoiced: { subject: Job }
`;

export const SYNTHETIC_MAPPING_YAML = `schema_version: 1
kind: mapping
mapping:
  name: jobs-csv
  source: { name: jobs, format: csv }
  entities:
    - entity: Job
      id: { field: id }
      properties:
        status: { field: status }
        assignee: { field: assignee }
    - entity: Person
      id: { field: assignee }
      properties:
        name: { field: assignee }
  events:
    - event: JobCompleted
      subject: { entity: Job, id: { field: id } }
      occurred_at: { field: done_at, as: datetime }
`;

export const SYNTHETIC_EVALUATION_YAML = `schema_version: 1
kind: evaluation
evaluation:
  name: latency
  interval: { start_event: JobCompleted, end_event: InvoiceIssued }
  metric: duration
  aggregation: median
  unit: days
  baseline: { value: 9 }
  target: { comparator: lt, value: 4 }
  guardrails: []
`;

export const SYNTHETIC_AGENT_YAML = `schema_version: 1
kind: agent
agent:
  name: t
  projection: CompletedButNotInvoiced
  task: { goal: g, deliverables: [explanation] }
  permitted_actions:
    SendReminder:
      params:
        target: { type: ref, ref: Person, required: true }
        related_entity: { type: ref, ref: Job, required: true }
`;

/** All four files, consistent with each other (Job/Person/Invoice refs). */
export const SYNTHETIC_BUNDLE_FILES: Record<string, string> = {
  "domain.yaml": SYNTHETIC_DOMAIN_YAML,
  "mapping.yaml": SYNTHETIC_MAPPING_YAML,
  "evaluation.yaml": SYNTHETIC_EVALUATION_YAML,
  "agent.yaml": SYNTHETIC_AGENT_YAML,
};

/** Open response whose file texts are the full synthetic bundle. */
export const SYNTHETIC_BUNDLE_OPEN_RESPONSE: OpenResponse = {
  ...SYNTHETIC_OPEN_RESPONSE,
  files: { ...SYNTHETIC_BUNDLE_FILES },
};

/** Contract vocabulary subset the domain editor needs. */
export const SYNTHETIC_CONTRACT = {
  api_version: "v1",
  core_version: "0.0.0",
  schema_version: 1,
  workspace_files: {},
  diagnostic_fields: [],
  operations: {},
  contract: {
    property_types: [
      "string",
      "integer",
      "number",
      "boolean",
      "date",
      "datetime",
      "ref",
    ],
  },
};

/**
 * SYNTHETIC FIXTURE — richer evaluation/agent texts so the structured
 * forms (#18) have real content to hydrate, including a comment that
 * proves form edits preserve untouched content.
 */
export const SYNTHETIC_FORM_EVALUATION_YAML = `schema_version: 1
kind: evaluation
# comment proving form edits preserve untouched content
evaluation:
  name: completion-to-invoice-latency
  description: Days from JobCompleted to InvoiceIssued for the same job.
  interval:
    start_event: JobCompleted
    end_event: InvoiceIssued
  metric: duration
  aggregation: median
  unit: days
  baseline:
    value: 9
    description: Baseline median completion-to-invoice latency.
  target:
    comparator: lt
    value: 4
    description: Strict target; a median of exactly 4 days is a miss.
  guardrails:
    - name: invoice-error-rate
      metric: invoice_error_rate
      comparator: not_increase
      description: Invoice-error rate must not increase versus baseline.
`;

export const SYNTHETIC_FORM_AGENT_YAML = `schema_version: 1
kind: agent
agent:
  name: billing-delay-diagnosis
  description: Diagnose completed-but-uninvoiced jobs and propose a reminder.
  projection: CompletedButNotInvoiced
  task:
    goal: Identify likely causes of delayed invoicing and suggest an intervention.
    deliverables:
      - explanation
      - hypotheses
      - follow_up_questions
      - action_proposals
  permitted_actions:
    SendReminder:
      description: Send a billing-readiness reminder to the assignee.
      params:
        target:
          type: ref
          ref: Person
          required: true
        reason:
          type: string
          required: true
`;

/** SYNTHETIC FIXTURE — mirrors service.contract_metadata() shape. */
export const SYNTHETIC_CONTRACT_RESPONSE: ContractResponse = {
  api_version: "v1",
  core_version: "0.0.0-synthetic",
  schema_version: 1,
  workspace_files: {
    domain: "domain.yaml",
    mapping: "mapping.yaml",
    evaluation: "evaluation.yaml",
    agent: "agent.yaml",
  },
  diagnostic_fields: [
    "severity",
    "code",
    "message",
    "file",
    "path",
    "line",
    "hint",
  ],
  operations: {},
  contract: {
    property_types: [
      "string",
      "integer",
      "number",
      "boolean",
      "date",
      "datetime",
      "ref",
    ],
    scalar_types: ["string", "integer", "number", "boolean", "date", "datetime"],
    cast_types: ["string", "integer", "number", "boolean", "date", "datetime"],
    source_formats: ["csv", "json"],
    condition_operators: {},
    evaluation: {
      metrics: ["duration"],
      aggregations: ["median", "mean", "min", "max", "count"],
      units: ["seconds", "minutes", "hours", "days"],
      comparators: ["lt", "lte", "gt", "gte"],
      guardrail_comparators: ["not_increase", "lt", "lte", "gt", "gte"],
    },
    deliverables: [
      "explanation",
      "hypotheses",
      "follow_up_questions",
      "action_proposals",
    ],
  },
};

/**
 * SYNTHETIC FIXTURE — open response with the full example definition texts
 * so the structured form tests exercise realistic YAML.
 */
export const SYNTHETIC_FORM_OPEN_RESPONSE: OpenResponse = {
  ...SYNTHETIC_OPEN_RESPONSE,
  files: {
    "domain.yaml": SYNTHETIC_DOMAIN_YAML,
    "mapping.yaml": SYNTHETIC_MAPPING_YAML,
    "evaluation.yaml": SYNTHETIC_FORM_EVALUATION_YAML,
    "agent.yaml": SYNTHETIC_FORM_AGENT_YAML,
  },
};

/** Build a fetch stub that returns a JSON response. */
export function stubFetchJson(status: number, body: unknown) {
  return (input: RequestInfo | URL, init?: RequestInit) => {
    void input;
    void init;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
  };
}
