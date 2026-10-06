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
  Diagnostic,
  DraftValidateResponse,
  OpenResponse,
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
