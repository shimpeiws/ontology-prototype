/**
 * Minimal typed client for the local ontology API (docs/api.md).
 *
 * Boundary rules from authoring-flow.md:
 * - API failures are structured errors and surface in error UI; they never
 *   enter the diagnostics channel, which carries core validation findings.
 * - Client-synthesized failures use `WEB.*` codes so they can never be
 *   confused with the server's `API.*` codes.
 * - Only `/api/v1` is called; there is no other path to workspace data.
 */

import type {
  ApiErrorBody,
  ContractResponse,
  DecisionResponse,
  DiagnoseRunResponse,
  DraftPreviewResponse,
  DraftValidateResponse,
  EvaluateRunTriggerResponse,
  EvaluationRunResponse,
  EvaluationRunsResponse,
  ExecuteResponse,
  IngestResponse,
  OpenResponse,
  ProjectRunResponse,
  ProposalResponse,
  ProposalsResponse,
  SaveResponse,
  TraceOverviewResponse,
  TraceViewResponse,
} from "./types";

const API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";

/** A transport-level failure: HTTP status plus a stable code. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number | null;
  /** True when retrying the same request could succeed (network, 5xx). */
  readonly retryable: boolean;
  /** Optional structured detail carried by the API error body. */
  readonly detail?: unknown;

  constructor(
    code: string,
    message: string,
    status: number | null,
    retryable: boolean,
    detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    this.detail = detail;
  }
}

function isErrorBody(value: unknown): value is { error: ApiErrorBody } {
  if (typeof value !== "object" || value === null) return false;
  const err = (value as { error?: unknown }).error;
  if (typeof err !== "object" || err === null) return false;
  const e = err as { code?: unknown; message?: unknown };
  return typeof e.code === "string" && typeof e.message === "string";
}

async function post<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(
      "WEB.API_UNREACHABLE",
      "Could not reach the local API. Check that `ontology-api` is running.",
      null,
      true,
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(
      "WEB.BAD_RESPONSE",
      `The API returned a non-JSON response (HTTP ${response.status}).`,
      response.status,
      response.status >= 500,
    );
  }

  if (!response.ok) {
    if (isErrorBody(payload)) {
      const { code, message, detail } = payload.error;
      throw new ApiError(
        code,
        message,
        response.status,
        response.status >= 500,
        detail,
      );
    }
    throw new ApiError(
      "WEB.BAD_RESPONSE",
      `The API returned HTTP ${response.status} without a structured error.`,
      response.status,
      response.status >= 500,
      payload,
    );
  }
  return payload as T;
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(
      "WEB.API_UNREACHABLE",
      "Could not reach the local API. Check that `ontology-api` is running.",
      null,
      true,
    );
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(
      "WEB.BAD_RESPONSE",
      `The API returned a non-JSON response (HTTP ${response.status}).`,
      response.status,
      response.status >= 500,
    );
  }
  if (!response.ok) {
    if (isErrorBody(payload)) {
      const { code, message, detail } = payload.error;
      throw new ApiError(
        code,
        message,
        response.status,
        response.status >= 500,
        detail,
      );
    }
    throw new ApiError(
      "WEB.BAD_RESPONSE",
      `The API returned HTTP ${response.status} without a structured error.`,
      response.status,
      response.status >= 500,
      payload,
    );
  }
  return payload as T;
}

export const api = {
  contract: (signal?: AbortSignal) =>
    get<ContractResponse>("/contract", signal),
  openWorkspace: (path: string, signal?: AbortSignal) =>
    post<OpenResponse>("/workspaces/open", { path }, signal),
  validateDraft: (
    path: string,
    files: Record<string, string>,
    signal?: AbortSignal,
  ) =>
    post<DraftValidateResponse>(
      "/workspaces/drafts/validate",
      { path, files },
      signal,
    ),
  saveWorkspace: (
    path: string,
    baseDigest: string,
    files: Record<string, string>,
    signal?: AbortSignal,
  ) =>
    post<SaveResponse>(
      "/workspaces/save",
      { path, base_digest: baseDigest, files },
      signal,
    ),
  previewDraft: (
    path: string,
    files: Record<string, string>,
    sample: { text: string; source?: string },
    signal?: AbortSignal,
  ) =>
    post<DraftPreviewResponse>(
      "/workspaces/drafts/preview",
      { path, files, sample },
      signal,
    ),

  /* Operational inspection (ADR 0003): read-only views over the
   * workspace's recorded runtime. All five are POSTs carrying
   * `{path}` plus the lookup key; none mutate state. */
  traceOverview: (path: string, signal?: AbortSignal) =>
    post<TraceOverviewResponse>("/workspaces/trace", { path }, signal),
  traceSubject: (path: string, subject: string, signal?: AbortSignal) =>
    post<TraceViewResponse>(
      "/workspaces/trace/subject",
      { path, subject },
      signal,
    ),
  traceRun: (path: string, runId: string, signal?: AbortSignal) =>
    post<TraceViewResponse>(
      "/workspaces/trace/run",
      { path, run_id: runId },
      signal,
    ),
  evaluationRuns: (path: string, signal?: AbortSignal) =>
    post<EvaluationRunsResponse>("/workspaces/evaluations", { path }, signal),
  evaluationRun: (path: string, runId: string, signal?: AbortSignal) =>
    post<EvaluationRunResponse>(
      "/workspaces/evaluations/run",
      { path, run_id: runId },
      signal,
    ),
  proposals: (path: string, signal?: AbortSignal) =>
    post<ProposalsResponse>("/workspaces/proposals", { path }, signal),
  proposal: (path: string, proposalId: string, signal?: AbortSignal) =>
    post<ProposalResponse>(
      "/workspaces/proposals/show",
      { path, proposal_id: proposalId },
      signal,
    ),

  /* The human decision write (ADR 0003): an approve/reject on a
   * persisted proposal, recorded through the core's `decide` and bound
   * to the exact payload digest. */
  decide: (
    path: string,
    proposalId: string,
    decision: "approved" | "rejected",
    reviewer: string,
    reason: string | null,
    signal?: AbortSignal,
  ) =>
    post<DecisionResponse>(
      "/workspaces/proposals/decide",
      { path, proposal_id: proposalId, decision, reviewer, reason },
      signal,
    ),

  /* Loop-stage triggers (ADR 0004): one trigger per stage, each
   * returning the CLI's `--json` payload plus `api_version`. Domain
   * outcomes arrive as `ok:false` payloads with core diagnostics;
   * lock contention throws STORAGE.WRITE_BUSY (retryable). */
  ingest: (
    path: string,
    snapshot: { text: string; name?: string; source?: string },
    signal?: AbortSignal,
  ) =>
    post<IngestResponse>("/workspaces/ingest", { path, snapshot }, signal),
  project: (
    path: string,
    asOf: string,
    projection?: string,
    signal?: AbortSignal,
  ) =>
    post<ProjectRunResponse>(
      "/workspaces/project",
      { path, as_of: asOf, projection },
      signal,
    ),
  diagnose: (
    path: string,
    source: { runId?: string; asOf?: string },
    options?: { provider?: string; model?: string },
    signal?: AbortSignal,
  ) =>
    post<DiagnoseRunResponse>(
      "/workspaces/diagnose",
      {
        path,
        run_id: source.runId ?? null,
        as_of: source.asOf ?? null,
        provider: options?.provider ?? "openrouter",
        model: options?.model ?? null,
      },
      signal,
    ),
  evaluate: (
    path: string,
    boundary: string,
    asOf: string,
    signal?: AbortSignal,
  ) =>
    post<EvaluateRunTriggerResponse>(
      "/workspaces/evaluate",
      { path, boundary, as_of: asOf },
      signal,
    ),
  execute: (path: string, proposalId: string, signal?: AbortSignal) =>
    post<ExecuteResponse>(
      "/workspaces/execute",
      { path, proposal_id: proposalId },
      signal,
    ),
};
