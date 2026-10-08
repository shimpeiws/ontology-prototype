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
  DraftPreviewResponse,
  DraftValidateResponse,
  EvaluationReport,
  EvaluationRunRecord,
  EvaluationRunsResponse,
  EvaluationRunResponse,
  OpenResponse,
  SaveResponse,
  SubjectTrace,
  TraceAnchor,
  TraceOverviewResponse,
  TraceViewResponse,
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
      sources: [
        {
          name: "jobs",
          format: "csv",
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
      ],
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
    CompletedButNotInvoiced:
      subject: Job
      select:
        required_events: [JobCompleted]
        absent_events: [InvoiceIssued]
        elapsed: { event: JobCompleted, comparator: gt, value: 2, unit: days }
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
    time_casts: ["date", "datetime"],
    source_formats: ["csv", "json"],
    preview_limits: { max_bytes: 262144, max_records: 500 },
    condition_operators: {
      comparison: ["eq", "gt", "gte", "lt", "lte", "ne"],
      set: ["in", "not_in"],
      null: ["is_null", "not_null"],
      logic: ["and", "not", "or"],
    },
    evaluation: {
      metrics: ["duration"],
      aggregations: ["median", "mean", "min", "max", "count"],
      units: ["seconds", "minutes", "hours", "days"],
      comparators: ["lt", "lte", "gt", "gte"],
      guardrail_comparators: ["not_increase", "lt", "lte", "gt", "gte"],
    },
    projection_selection: {
      comparators: ["lt", "lte", "gt", "gte"],
      duration_units: ["seconds", "minutes", "hours", "days"],
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

/**
 * SYNTHETIC FIXTURE — a richer mapping text for the structured mapping
 * form (#17): two entity bindings, two gated events with `when`
 * conditions, an optional observed_at assignment, and a comment proving
 * form edits preserve untouched content.
 */
export const SYNTHETIC_MAPPING_FORM_YAML = `schema_version: 1
kind: mapping
# comment proving form edits preserve untouched content
mapping:
  name: jobs-csv
  description: Jobs CSV to canonical entities and events.
  source: { name: jobs, format: csv }
  entities:
    - entity: Job
      id: { field: id }
      properties:
        status: { field: status, as: string }
        assignee: { field: assignee }
    - entity: Person
      id: { field: assignee }
      properties:
        name: { field: assignee }
  events:
    - event: JobCompleted
      subject: { entity: Job, id: { field: id } }
      when:
        op: and
        args:
          - { op: eq, field: status, value: completed }
          - { op: not_null, field: completed_at }
      occurred_at: { field: completed_at, as: datetime }
    - event: InvoiceIssued
      subject: { entity: Job, id: { field: id } }
      when: { op: not_null, field: invoiced_at }
      occurred_at: { field: invoiced_at, as: datetime }
      observed_at: { field: observed_ts, as: datetime }
`;

/** Open response whose mapping.yaml is the form-test mapping. */
export const SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE: OpenResponse = {
  ...SYNTHETIC_FORM_OPEN_RESPONSE,
  files: {
    ...SYNTHETIC_FORM_OPEN_RESPONSE.files,
    "mapping.yaml": SYNTHETIC_MAPPING_FORM_YAML,
  },
};

/**
 * SYNTHETIC FIXTURE — the `sources:` form (#57): two declared sources,
 * each carrying its own format and entity/event bindings.
 */
export const SYNTHETIC_MULTI_SOURCE_MAPPING_YAML = `schema_version: 1
kind: mapping
mapping:
  name: jobs-invoices
  sources:
    - name: jobs
      format: csv
      entities:
        - entity: Job
          id: { field: id }
          properties:
            status: { field: status }
      events:
        - event: JobCompleted
          subject: { entity: Job, id: { field: id } }
          occurred_at: { field: completed_at, as: datetime }
    - name: invoices
      format: json
      entities:
        - entity: Invoice
          id: { field: id }
          properties:
            job: { field: job }
      events:
        - event: InvoiceIssued
          subject: { entity: Job, id: { field: job } }
          occurred_at: { field: issued_at, as: datetime }
`;

/** Open response whose mapping.yaml declares two sources. */
export const SYNTHETIC_MULTI_SOURCE_OPEN_RESPONSE: OpenResponse = {
  ...SYNTHETIC_FORM_OPEN_RESPONSE,
  files: {
    ...SYNTHETIC_FORM_OPEN_RESPONSE.files,
    "mapping.yaml": SYNTHETIC_MULTI_SOURCE_MAPPING_YAML,
  },
};

/** SYNTHETIC FIXTURE — a preview response shaped after service._preview_payload. */
export const SYNTHETIC_PREVIEW_RESPONSE: DraftPreviewResponse = {
  api_version: "v1",
  valid: true,
  digest: "sha256:draft-bundle",
  diagnostics: [],
  preview: {
    preview_id: "prev-aaaabbbbcccc-dddd",
    source: "jobs",
    format: "csv",
    input_digest: "sha256:sample",
    mapping_digest: "sha256:mapping",
    observed_at: "2026-10-05T12:00:00Z",
    record_count: 2,
    truncated: false,
    observations: [
      {
        observation_id: "obs-aaaa",
        ingest_id: "prev-aaaabbbbcccc-dddd",
        source: "jobs.csv",
        row: 2,
        status: "accepted",
        observed_at: "2026-10-05T12:00:00Z",
        content_digest: "sha256:sample",
        mapping_digest: "sha256:mapping",
        record: {
          id: "101",
          status: "completed",
          assignee: "taro",
          completed_at: "2026-10-01",
          invoiced_at: null,
        },
        entities: [
          {
            entity: "Job",
            id: "101",
            properties: { status: "completed", assignee: "taro" },
          },
        ],
        events: [
          {
            event: "JobCompleted",
            subject: { entity: "Job", id: "101" },
            occurred_at: "2026-10-01T00:00:00Z",
            observed_at: "2026-10-05T12:00:00Z",
          },
        ],
        diagnostics: [],
      },
      {
        observation_id: "obs-bbbb",
        ingest_id: "prev-aaaabbbbcccc-dddd",
        source: "jobs.csv",
        row: 3,
        status: "rejected",
        observed_at: "2026-10-05T12:00:00Z",
        content_digest: "sha256:sample",
        mapping_digest: "sha256:mapping",
        record: {
          id: "",
          status: "in_progress",
          assignee: "hanako",
          completed_at: null,
          invoiced_at: null,
        },
        entities: [],
        events: [],
        diagnostics: [
          {
            severity: "error",
            code: "INGEST.MISSING_ID",
            message: "entity 'Job' resolved no id",
            file: "jobs.csv",
            path: "record[3].id",
            line: null,
            hint: null,
          },
        ],
      },
    ],
    diagnostics: [],
  },
};

/* ---- Operational inspection fixtures (#62) — shaped after the #61
 * contract in docs/api.md (the core's trace/evaluation payloads plus
 * `api_version`). */

export const SYNTHETIC_TRACE_OVERVIEW: TraceOverviewResponse = {
  api_version: "v1",
  workspace: "/abs/example/job-invoicing",
  ok: true,
  journal: { events: 14, unreadable_lines: 1, failed_events: 1 },
  evidence: {
    ingest_runs: ["ing-20261005T120000000000Z-aaaa0000"],
    observations: 3,
    snapshots: 1,
  },
  canonical: { entities: 3, facts: 4, fact_links: 1 },
  projection_runs: [
    {
      run_id: "prj-20261005T120500000000Z-bbbb1111",
      projection: "CompletedButNotInvoiced",
      as_of: "2026-10-05T00:00:00Z",
      candidates: 2,
    },
  ],
  diagnosis_runs: [
    {
      run_id: "dgx-20261005T120600000000Z-cccc2222",
      projection_run_id: "prj-20261005T120500000000Z-bbbb1111",
      provider: "stub",
      model: "stub-offline",
      status: "ok",
    },
    {
      run_id: "dgx-20261005T120700000000Z-dddd3333",
      projection_run_id: "prj-20261005T120500000000Z-bbbb1111",
      provider: "openrouter",
      model: "openai/gpt-4o-mini",
      status: "failed",
    },
  ],
  proposals: {
    total: 2,
    by_status: { approved: 1, unindexed: 1 },
    unindexed: 1,
  },
  decisions: 1,
  executions: { attempts: 2, by_status: { succeeded: 1, failed: 1 } },
  evaluation_runs: [
    {
      run_id: "evl-20261015T000000000000Z-eeee4444",
      evaluation: "completion-to-invoice-latency",
      as_of: "2026-10-15T00:00:00Z",
      overall: "not_met",
    },
  ],
  links: { total: 21, unresolved: 1 },
  diagnostics: [
    {
      severity: "warning",
      code: "TRACE.JOURNAL_CORRUPT",
      message: "trace.jsonl line 9 is not valid JSON; skipped",
      file: null,
      path: null,
      line: null,
      hint: null,
    },
    {
      severity: "warning",
      code: "TRACE.UNRESOLVED_LINK",
      message:
        "fact-later-0001 references dgx-missing via correlated_intervention, but the target is in no stored artifact or journal event",
      file: null,
      path: null,
      line: null,
      hint: null,
    },
  ],
};

export const SYNTHETIC_TRACE_OVERVIEW_EMPTY: TraceOverviewResponse = {
  api_version: "v1",
  workspace: "/abs/example/job-invoicing",
  ok: true,
  journal: { events: 0, unreadable_lines: 0, failed_events: 0 },
  evidence: { ingest_runs: [], observations: 0, snapshots: 0 },
  canonical: { entities: 0, facts: 0, fact_links: 0 },
  projection_runs: [],
  diagnosis_runs: [],
  proposals: { total: 0, by_status: {}, unindexed: 0 },
  decisions: 0,
  executions: { attempts: 0, by_status: {} },
  evaluation_runs: [],
  links: { total: 0, unresolved: 0 },
  diagnostics: [],
};

export const SYNTHETIC_SUBJECT_TRACE: SubjectTrace = {
  subject: { entity: "Job", id: "101" },
  entity: {
    entity_type: "Job",
    entity_id: "101",
    properties: { status: "completed", assignee: "taro" },
    first_observation_id: "obs-0001",
    last_observation_id: "obs-0001",
    last_ingest_id: "ing-20261005T120000000000Z-aaaa0000",
    updated_at: "2026-10-05T12:00:00Z",
    properties_changed_at: "2026-10-05T12:00:00Z",
  },
  observations: [
    {
      observation_id: "obs-0001",
      ingest_id: "ing-20261005T120000000000Z-aaaa0000",
      source: "jobs.csv",
      row: 2,
      status: "accepted",
      observed_at: "2026-10-05T12:00:00Z",
      content_digest: "sha256:sample",
      mapping_digest: "sha256:mapping",
      record: { id: "101", status: "completed", assignee: "taro" },
      entities: [
        { entity: "Job", id: "101", properties: { status: "completed" } },
      ],
      events: [
        {
          event: "JobCompleted",
          subject: { entity: "Job", id: "101" },
          occurred_at: "2026-10-01T00:00:00Z",
          observed_at: "2026-10-05T12:00:00Z",
        },
      ],
      diagnostics: [],
    },
  ],
  facts: [
    {
      fact_key: "fact-s1",
      event: "JobCompleted",
      subject: { entity: "Job", id: "101" },
      occurred_at: "2026-10-01T00:00:00Z",
      observed_at: "2026-10-05T12:00:00Z",
      first_observation_id: "obs-0001",
      ingest_id: "ing-20261005T120000000000Z-aaaa0000",
      observation_ids: ["obs-0001"],
      ingest_ids: ["ing-20261005T120000000000Z-aaaa0000"],
      links: [],
    },
    {
      fact_key: "fact-later-0001",
      event: "InvoiceIssued",
      subject: { entity: "Job", id: "101" },
      occurred_at: "2026-10-09T00:00:00Z",
      observed_at: "2026-10-12T12:00:00Z",
      first_observation_id: "obs-0009",
      ingest_id: "ing-20261012T120000000000Z-bbbb0000",
      observation_ids: ["obs-0009"],
      ingest_ids: ["ing-20261012T120000000000Z-bbbb0000"],
      links: [
        {
          fact_key: "fact-later-0001",
          relation: "correlated_intervention",
          target: "dgx-20261005T120600000000Z-cccc2222-p0",
          observation_id: "obs-0009",
          ingest_id: "ing-20261012T120000000000Z-bbbb0000",
        },
      ],
    },
  ],
  ingests: [
    "ing-20261005T120000000000Z-aaaa0000",
    "ing-20261012T120000000000Z-bbbb0000",
  ],
  snapshots: ["sha256:sample"],
  projections: [
    {
      run_id: "prj-20261005T120500000000Z-bbbb1111",
      projection: "CompletedButNotInvoiced",
      as_of: "2026-10-05T00:00:00Z",
      created_at: "2026-10-05T12:05:00Z",
      definition_digest: "sha256:domain",
      role: "candidate",
      evidence: { fact_key: "fact-s1" },
    },
  ],
  diagnoses: [
    {
      run_id: "dgx-20261005T120600000000Z-cccc2222",
      projection_run_id: "prj-20261005T120500000000Z-bbbb1111",
      provider: "stub",
      model: "stub-offline",
      status: "ok",
      provider_called: true,
      prompt_version: "v1",
      input_digest: "sha256:input",
      deliverables: { explanation: "…" },
      rejected: [],
      proposals: ["dgx-20261005T120600000000Z-cccc2222-p0"],
      created_at: "2026-10-05T12:06:00Z",
    },
    {
      run_id: "dgx-20261005T120700000000Z-dddd3333",
      projection_run_id: "prj-20261005T120500000000Z-bbbb1111",
      provider: "openrouter",
      model: "openai/gpt-4o-mini",
      status: "failed",
      provider_called: true,
      prompt_version: "v1",
      input_digest: "sha256:input",
      deliverables: {},
      rejected: [],
      proposals: [],
      created_at: "2026-10-05T12:07:00Z",
    },
  ],
  proposals: [
    {
      proposal_id: "dgx-20261005T120600000000Z-cccc2222-p0",
      diagnosis_run_id: "dgx-20261005T120600000000Z-cccc2222",
      proposal_index: 0,
      action: "SendReminder",
      params: { target: "taro", reason: "invoice delayed" },
      reason: "Job 101 completed 4 days ago without an invoice.",
      subject_entity: "Job",
      subject_id: "101",
      related_entities: [{ entity: "Person", id: "taro" }],
      evidence: { fact_key: "fact-s1" },
      payload_digest: "sha256:payload",
      created_at: "2026-10-05T12:06:00Z",
      decisions: [
        {
          decision_id: "dec-20261005T130000000000Z-ffff5555",
          proposal_id: "dgx-20261005T120600000000Z-cccc2222-p0",
          decision: "approved",
          reviewer: "fde-sato",
          reason: null,
          payload_digest: "sha256:payload",
          decided_at: "2026-10-05T13:00:00Z",
        },
      ],
      executions: [
        {
          execution_id: "exe-20261005T131000000000Z-aaaa6666",
          idempotency_key: "idem-20261005T131000000000Z-bbbb7777",
          proposal_id: "dgx-20261005T120600000000Z-cccc2222-p0",
          action: "SendReminder",
          adapter: "csv",
          attempt: 1,
          status: "succeeded",
          receipt: { path: ".ontology/reminders.csv", row: 2 },
          error: null,
          created_at: "2026-10-05T13:10:00Z",
        },
      ],
      correlated_facts: ["fact-later-0001"],
      status: "approved",
      indexed: true,
    },
    {
      proposal_id: "dgx-20261005T120600000000Z-cccc2222-pos1",
      diagnosis_run_id: "dgx-20261005T120600000000Z-cccc2222",
      proposal_index: 1,
      action: "SendReminder",
      params: { target: "hanako" },
      reason: "Job 103 is also waiting.",
      subject_entity: null,
      subject_id: null,
      evidence: {},
      decisions: [],
      executions: [],
      correlated_facts: [],
      status: "unindexed",
      indexed: false,
    },
  ],
  decisions: [
    {
      decision_id: "dec-20261005T130000000000Z-ffff5555",
      proposal_id: "dgx-20261005T120600000000Z-cccc2222-p0",
      decision: "approved",
      reviewer: "fde-sato",
      reason: null,
      payload_digest: "sha256:payload",
      decided_at: "2026-10-05T13:00:00Z",
    },
  ],
  executions: [
    {
      execution_id: "exe-20261005T131000000000Z-aaaa6666",
      idempotency_key: "idem-20261005T131000000000Z-bbbb7777",
      proposal_id: "dgx-20261005T120600000000Z-cccc2222-p0",
      action: "SendReminder",
      adapter: "csv",
      attempt: 1,
      status: "succeeded",
      receipt: { path: ".ontology/reminders.csv", row: 2 },
      error: null,
      created_at: "2026-10-05T13:10:00Z",
    },
  ],
  correlated_facts: ["fact-later-0001"],
  evaluations: [
    {
      run_id: "evl-20261015T000000000000Z-eeee4444",
      evaluation: "completion-to-invoice-latency",
      as_of: "2026-10-15T00:00:00Z",
      boundary: "2026-10-05T00:00:00Z",
      contract_digest: "sha256:contract",
      overall: "not_met",
      subject: { disposition: "paired", cohort: "baseline" },
    },
  ],
  journal: [
    {
      ts: "2026-10-05T12:00:00Z",
      stage: "ingest",
      kind: "ingest.completed",
      data: { ingest_id: "ing-20261005T120000000000Z-aaaa0000" },
    },
    {
      ts: "2026-10-05T12:07:00Z",
      stage: "diagnosis",
      kind: "diagnosis.failed",
      data: { run_id: "dgx-20261005T120700000000Z-dddd3333" },
    },
  ],
  links: [
    {
      from: "obs-0001",
      to: "ing-20261005T120000000000Z-aaaa0000",
      via: "ingest_run",
      resolved: true,
    },
    {
      from: "obs-0001",
      to: "fact-s1",
      via: "asserted",
      resolved: true,
    },
    {
      from: "fact-later-0001",
      to: "dgx-missing",
      via: "correlated_intervention",
      resolved: false,
    },
  ],
  unresolved: [
    {
      from: "fact-later-0001",
      to: "dgx-missing",
      via: "correlated_intervention",
      resolved: false,
    },
  ],
};

export const SYNTHETIC_ANCHOR_TRACE: TraceAnchor = {
  id: "dgx-20261005T120700000000Z-dddd3333",
  kind: "diagnosis_run",
  nature: "ai",
  record: {
    run_id: "dgx-20261005T120700000000Z-dddd3333",
    provider: "openrouter",
    model: "openai/gpt-4o-mini",
    status: "failed",
    diagnostics: [
      { code: "DIAGNOSIS.PROVIDER_ERROR", message: "provider call failed" },
    ],
  },
  links: [
    {
      from: "dgx-20261005T120700000000Z-dddd3333",
      to: "prj-20261005T120500000000Z-bbbb1111",
      via: "input_run",
      resolved: true,
    },
  ],
  unresolved: [],
  journal: [
    {
      ts: "2026-10-05T12:07:00Z",
      stage: "diagnosis",
      kind: "diagnosis.failed",
      data: { run_id: "dgx-20261005T120700000000Z-dddd3333" },
    },
  ],
  subjects: ["Job/101", "Job/103"],
};

export const SYNTHETIC_TRACE_SUBJECT_RESPONSE: TraceViewResponse = {
  api_version: "v1",
  workspace: "/abs/example/job-invoicing",
  ok: true,
  trace: SYNTHETIC_SUBJECT_TRACE,
  diagnostics: [],
};

export const SYNTHETIC_TRACE_RUN_RESPONSE: TraceViewResponse = {
  api_version: "v1",
  workspace: "/abs/example/job-invoicing",
  ok: true,
  trace: SYNTHETIC_ANCHOR_TRACE,
  diagnostics: [],
};

export const SYNTHETIC_TRACE_INVALID_SUBJECT: TraceViewResponse = {
  api_version: "v1",
  workspace: "/abs/example/job-invoicing",
  ok: false,
  diagnostics: [
    {
      severity: "error",
      code: "TRACE.INVALID_SUBJECT",
      message: "invalid --subject 'bogus': expected 'Entity/Id'",
      file: null,
      path: null,
      line: null,
      hint: null,
    },
  ],
};

/** A frozen report whose after cohort missed the strict target. */
export const SYNTHETIC_EVALUATION_REPORT: EvaluationReport = {
  evaluation: "completion-to-invoice-latency",
  contract_digest: "sha256:contract",
  as_of: "2026-10-15T00:00:00Z",
  cohort_boundary: "2026-10-05T00:00:00Z",
  interval: {
    start_event: "JobCompleted",
    end_event: "InvoiceIssued",
    subject: "Job",
  },
  metric: "duration",
  aggregation: "median",
  unit: "days",
  declared_baseline: 9,
  cohorts: {
    baseline: {
      paired: [
        {
          subject_id: "101",
          start_fact_key: "fact-s1",
          end_fact_key: "fact-e1",
          started_at: "2026-10-01T00:00:00Z",
          ended_at: "2026-10-09T00:00:00Z",
          duration_seconds: 691200,
          duration: 8,
        },
      ],
      pending: [],
      invalid: [
        {
          subject_id: "099",
          reason:
            "every end event precedes the start event; pairing would produce a negative interval",
          start_fact_key: "fact-s9",
          end_fact_keys: ["fact-e9"],
        },
      ],
      ambiguous: [],
      counts: { paired: 1, pending: 0, invalid: 1, ambiguous: 0 },
      sample: 1,
      aggregate: 8,
    },
    after: {
      paired: [
        {
          subject_id: "102",
          start_fact_key: "fact-s2",
          end_fact_key: "fact-e2",
          started_at: "2026-10-06T00:00:00Z",
          ended_at: "2026-10-12T00:00:00Z",
          duration_seconds: 518400,
          duration: 6,
          closes_after_boundary: true,
        },
        {
          subject_id: "103",
          start_fact_key: "fact-s3",
          end_fact_key: "fact-e3",
          started_at: "2026-10-07T00:00:00Z",
          ended_at: "2026-10-11T00:00:00Z",
          duration_seconds: 345600,
          duration: 4,
          extra_end_fact_keys: ["fact-e3b"],
        },
      ],
      pending: [
        {
          subject_id: "104",
          start_fact_key: "fact-s4",
          started_at: "2026-10-10T00:00:00Z",
          elapsed_seconds: 432000,
          elapsed: 5,
        },
      ],
      invalid: [],
      ambiguous: [
        {
          subject_id: "105",
          reason: "end events on both sides of the start event",
          start_fact_keys: ["fact-s5"],
          end_fact_keys: ["fact-e5a", "fact-e5b"],
        },
      ],
      counts: { paired: 2, pending: 1, invalid: 0, ambiguous: 1 },
      sample: 2,
      aggregate: 5,
    },
  },
  unpaired_ends: [{ subject_id: "106", end_fact_keys: ["fact-e6"] }],
  change: { status: "measured", value: 3, direction: "improvement" },
  target: { comparator: "lt", value: 4, status: "missed" },
  guardrails: [
    {
      name: "invoice-error-rate",
      metric: "invoice_error_rate",
      comparator: "not_increase",
      status: "passed",
      evidence: {
        baseline: { rate: 0.1, errors: 1, invoices: 10 },
        after: { rate: 0.05, errors: 1, invoices: 20 },
      },
    },
    {
      name: "volume-check",
      metric: "job_volume",
      comparator: "lt",
      status: "unavailable",
      reason: "no registered semantics for metric 'job_volume'",
    },
  ],
  overall: { status: "not_met" },
  inputs: [
    {
      subject_id: "101",
      start_fact_keys: ["fact-s1"],
      end_fact_keys: ["fact-e1"],
      disposition: "paired",
      cohort: "baseline",
    },
    {
      subject_id: "107",
      start_fact_keys: [],
      end_fact_keys: [],
      future_start_fact_keys: ["fact-s7"],
      disposition: "excluded",
      excluded: "future-dated start event",
    },
  ],
  note: "Cohorts are assigned by when each interval's start event occurred relative to the cohort boundary. This report records an observed difference between cohorts; it does not establish that any intervention caused it.",
};

/** A report with no measured intervals — honest unavailability. */
export const SYNTHETIC_EVALUATION_REPORT_UNVERIFIED: EvaluationReport = {
  ...SYNTHETIC_EVALUATION_REPORT,
  cohorts: {
    baseline: {
      ...SYNTHETIC_EVALUATION_REPORT.cohorts.baseline,
      paired: [],
      invalid: [],
      counts: { paired: 0, pending: 0, invalid: 0, ambiguous: 0 },
      sample: 0,
      aggregate: null,
    },
    after: {
      paired: [],
      pending: [],
      invalid: [],
      ambiguous: [],
      counts: { paired: 0, pending: 0, invalid: 0, ambiguous: 0 },
      sample: 0,
      aggregate: null,
    },
  },
  unpaired_ends: [],
  inputs: [],
  change: {
    status: "unavailable",
    reason: "no measured intervals in the baseline and after cohort",
  },
  target: {
    comparator: "lt",
    value: 4,
    status: "unverified",
    reason: "the after cohort has no measured intervals",
  },
  guardrails: [
    {
      name: "invoice-error-rate",
      metric: "invoice_error_rate",
      comparator: "not_increase",
      status: "unavailable",
      reason: "no invoice-error evidence for the after cohort",
    },
  ],
  overall: { status: "unverified" },
};

export const SYNTHETIC_EVALUATION_RUN_RECORD: EvaluationRunRecord = {
  run_id: "evl-20261015T000000000000Z-eeee4444",
  evaluation: "completion-to-invoice-latency",
  as_of: "2026-10-15T00:00:00Z",
  boundary: "2026-10-05T00:00:00Z",
  created_at: "2026-10-15T00:00:01Z",
  contract_digest: "sha256:contract",
  report: SYNTHETIC_EVALUATION_REPORT,
};

export const SYNTHETIC_EVALUATION_RUN_UNVERIFIED: EvaluationRunRecord = {
  run_id: "evl-20261016T000000000000Z-ffff5555",
  evaluation: "completion-to-invoice-latency",
  as_of: "2026-10-16T00:00:00Z",
  boundary: "2026-10-06T00:00:00Z",
  created_at: "2026-10-16T00:00:01Z",
  contract_digest: "sha256:contract",
  report: SYNTHETIC_EVALUATION_REPORT_UNVERIFIED,
};

export const SYNTHETIC_EVALUATION_RUNS: EvaluationRunsResponse = {
  api_version: "v1",
  workspace: "/abs/example/job-invoicing",
  ok: true,
  evaluation_runs: [
    SYNTHETIC_EVALUATION_RUN_RECORD,
    SYNTHETIC_EVALUATION_RUN_UNVERIFIED,
  ],
};

export const SYNTHETIC_EVALUATION_RUNS_EMPTY: EvaluationRunsResponse = {
  api_version: "v1",
  workspace: "/abs/example/job-invoicing",
  ok: true,
  evaluation_runs: [],
};

export const SYNTHETIC_EVALUATION_RUN_RESPONSE: EvaluationRunResponse = {
  api_version: "v1",
  workspace: "/abs/example/job-invoicing",
  ok: true,
  run: SYNTHETIC_EVALUATION_RUN_RECORD,
};

export const SYNTHETIC_EVALUATION_UNKNOWN_RUN: EvaluationRunResponse = {
  api_version: "v1",
  workspace: "/abs/example/job-invoicing",
  ok: false,
  diagnostics: [
    {
      severity: "error",
      code: "EVALUATION.UNKNOWN_RUN",
      message: "no frozen evaluation report 'evl-bogus'",
      file: null,
      path: null,
      line: null,
      hint: "reports are recorded only when an evaluation completes without errors",
    },
  ],
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
