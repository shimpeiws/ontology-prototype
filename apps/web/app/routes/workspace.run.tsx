/**
 * Run view (#85–#89 / ADR 0004): the whole operational loop on one
 * screen, in stage order — ingest → project → diagnose → review /
 * execute → evaluate.
 *
 * Every stage card shows its last recorded run from the shared trace
 * view; the next available action is called out at the top and marked
 * on its card. Blocked states name what they are waiting on — invalid
 * definitions, no candidates, no proposal, or an unapproved proposal.
 * Trigger results render in place; inspection is one deep link away
 * (Trace, Proposals, Reports).
 */

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { api, ApiError } from "../lib/api";
import type {
  DiagnoseRunResponse,
  Diagnostic,
  EvaluateRunTriggerResponse,
  IngestResponse,
  ProjectRunResponse,
  ProposalsResponse,
  ProposalView,
  TraceOverviewResponse,
} from "../lib/types";
import { DiagnosticsList } from "../components/DiagnosticsList";
import { ExecuteControl } from "../components/ExecuteControl";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { StatusBadge } from "../components/StatusBadge";
import { useContract } from "../lib/useContract";
import { useWorkspace } from "../workspace/context";

export function meta() {
  return [{ title: "ontology — run the loop" }];
}

function toError(err: unknown): ApiError | Error {
  return err instanceof Error ? err : new Error(String(err));
}

type StageResult<R> =
  | { kind: "done"; response: R }
  | { kind: "domain"; diagnostics: Diagnostic[] }
  | { kind: "transport"; error: ApiError | Error };

type Overview =
  | { status: "loading" }
  | { status: "failed"; error: ApiError | Error }
  | { status: "done"; trace: TraceOverviewResponse; proposals: ProposalsResponse };

/** The stage the loop is waiting on, plus why — the next-action banner. */
interface LoopPosition {
  stage:
    | "definitions"
    | "ingest"
    | "project"
    | "diagnose"
    | "review"
    | "execute"
    | "evaluate"
    | "done";
  label: string;
  detail: string;
  /** Deep link for human steps that live on another surface. */
  to?: string;
}

/** Timestamp prefix of a run id (`ing-20261011T204146…Z-…`) — run ids
 * embed their recording instant, so a newer upstream artifact makes an
 * older downstream run stale. */
const runStamp = (id: string | null | undefined) =>
  id?.match(/-(\d{8}T\d+Z)/)?.[1] ?? "";

function loopPosition(
  trace: TraceOverviewResponse | null,
  proposals: ProposalView[],
): LoopPosition {
  if (!trace || !trace.ok) {
    return {
      stage: "definitions",
      label: "Fix the workspace definitions",
      detail:
        "Every stage runs against the validated bundle — resolve the load diagnostics first.",
      to: "/workspace",
    };
  }
  const lastIngest = trace.evidence?.ingest_runs?.at(-1);
  if (!lastIngest) {
    return {
      stage: "ingest",
      label: "Ingest a source snapshot",
      detail: "No observations are recorded yet — the loop starts with evidence.",
    };
  }
  const lastProjection = trace.projection_runs?.at(-1);
  if (!lastProjection) {
    return {
      stage: "project",
      label: "Run a projection",
      detail: "Observations are recorded; no frozen projection run exists yet.",
    };
  }
  if (runStamp(lastIngest) > runStamp(lastProjection.run_id)) {
    return {
      stage: "project",
      label: "Re-run the projection",
      detail:
        "Evidence was ingested after the last frozen run — project the new state before diagnosing.",
    };
  }
  const lastDiagnosis = trace.diagnosis_runs?.at(-1);
  const diagnosisRan = lastDiagnosis !== undefined;
  if (lastProjection.candidates === 0 && !proposals.length) {
    return {
      stage: "project",
      label: "Re-run the projection",
      detail:
        "The last projection selected no candidates — adjust the as-of or ingest more evidence before diagnosing.",
    };
  }
  if (!diagnosisRan && !proposals.length) {
    return {
      stage: "diagnose",
      label: "Run a diagnosis",
      detail: `${lastProjection.candidates} candidate(s) wait in run ${lastProjection.run_id}.`,
    };
  }
  if (!proposals.length) {
    return {
      stage: "diagnose",
      label: "Re-run the diagnosis",
      detail:
        "No persisted proposals — the last diagnosis produced none; check its rejected deliverables.",
    };
  }
  const executed = proposals.filter((p) =>
    p.executions.some((e) => e.status === "succeeded"),
  );
  const awaitingApproval = proposals.filter(
    (p) => p.status === "proposed",
  );
  const awaitingExecution = proposals.filter(
    (p) => p.status === "approved" && !executed.includes(p),
  );
  if (awaitingApproval.length) {
    return {
      stage: "review",
      label: "Review the open proposals",
      detail: `${awaitingApproval.length} proposal(s) await a human decision — execution stays gated until one approves.`,
      to: "/workspace/proposals",
    };
  }
  if (awaitingExecution.length) {
    return {
      stage: "execute",
      label: "Execute the approved proposal",
      detail: `${awaitingExecution.length} approved proposal(s) carry no successful write-back yet.`,
      to: "/workspace/proposals",
    };
  }
  const lastEvaluation = trace.evaluation_runs?.at(-1);
  if (!lastEvaluation) {
    return {
      stage: "evaluate",
      label: "Run an outcome evaluation",
      detail: "Write-back succeeded — measure the cohorts against the declared boundary.",
    };
  }
  const newestUpstream = [
    lastIngest,
    lastProjection.run_id,
    lastDiagnosis?.run_id,
  ]
    .map(runStamp)
    .reduce((a, b) => (a > b ? a : b), "");
  if (newestUpstream > runStamp(lastEvaluation.run_id)) {
    return {
      stage: "evaluate",
      label: "Re-run the evaluation",
      detail:
        "Evidence or runs landed after the last frozen report — freeze a fresh evaluation to close the loop.",
    };
  }
  return {
    stage: "done",
    label: "The loop is complete",
    detail:
      "Every stage has a recorded run — ingest new evidence or re-run any stage to continue.",
  };
}

function ResultBlock<R extends { diagnostics?: Diagnostic[] }>({
  result,
  onRetry,
  children,
}: {
  result: StageResult<R> | null;
  onRetry?: () => void;
  children?: (response: R) => ReactNode;
}) {
  if (!result) return null;
  if (result.kind === "domain") {
    return <DiagnosticsList diagnostics={result.diagnostics} />;
  }
  if (result.kind === "transport") {
    return (
      <ErrorState
        error={result.error}
        onRetry={
          result.error instanceof ApiError && result.error.retryable
            ? onRetry
            : undefined
        }
      />
    );
  }
  return (
    <div className="run-result" role="status">
      {children?.(result.response)}
      {result.response.diagnostics?.length ? (
        <DiagnosticsList diagnostics={result.response.diagnostics} />
      ) : null}
    </div>
  );
}

export default function RunPage() {
  const { state } = useWorkspace();
  const path = state.workspace?.requestedPath ?? null;
  const definitions = state.workspace?.definitions ?? null;
  const { contract } = useContract();

  const [overview, setOverview] = useState<Overview>({ status: "loading" });
  const [seq, setSeq] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const overviewPath = useRef<string | null>(null);

  // Trigger state per stage — pending plus the last rendered result.
  const [ingestResult, setIngestResult] =
    useState<StageResult<IngestResponse> | null>(null);
  const [projectResult, setProjectResult] =
    useState<StageResult<ProjectRunResponse> | null>(null);
  const [diagnoseResult, setDiagnoseResult] =
    useState<StageResult<DiagnoseRunResponse> | null>(null);
  const [evaluateResult, setEvaluateResult] =
    useState<StageResult<EvaluateRunTriggerResponse> | null>(null);
  const [pendingStage, setPendingStage] = useState<string | null>(null);

  // Stage forms.
  const [snapshotText, setSnapshotText] = useState("");
  const [snapshotName, setSnapshotName] = useState("");
  const [snapshotSource, setSnapshotSource] = useState("");
  const [asOf, setAsOf] = useState("");
  const [projectionName, setProjectionName] = useState("");
  const [diagSource, setDiagSource] = useState<"run" | "as_of">("run");
  const [diagAsOf, setDiagAsOf] = useState("");
  const [provider, setProvider] = useState("stub");
  const [model, setModel] = useState("");
  const [boundary, setBoundary] = useState("");
  const [evalAsOf, setEvalAsOf] = useState("");

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    const refetch = overviewPath.current === path;
    overviewPath.current = path;
    if (refetch) setRefreshing(true);
    else setOverview({ status: "loading" });
    Promise.all([
      api.traceOverview(path, controller.signal),
      api.proposals(path, controller.signal),
    ])
      .then(([trace, proposals]) =>
        setOverview({ status: "done", trace, proposals }),
      )
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setOverview({ status: "failed", error: toError(err) });
      })
      .finally(() => {
        if (!controller.signal.aborted) setRefreshing(false);
      });
    return () => {
      controller.abort();
      setRefreshing(false);
    };
  }, [path, seq]);

  const refresh = () => setSeq((n) => n + 1);

  // Discard a trigger result and reset stage UI when the workspace is
  // switched mid-flight — a response for workspace A must never render
  // on workspace B.
  const pathRef = useRef(path);
  pathRef.current = path;
  useEffect(() => {
    setIngestResult(null);
    setProjectResult(null);
    setDiagnoseResult(null);
    setEvaluateResult(null);
    setPendingStage(null);
  }, [path]);

  const trace =
    overview.status === "done" ? overview.trace : null;
  const proposals =
    overview.status === "done" ? (overview.proposals.proposals ?? []) : [];
  const position = loopPosition(trace, proposals);

  const sources = definitions?.mapping.sources ?? [];
  const projections = definitions
    ? Object.keys(definitions.domain.projections)
    : [];
  const defaultProjection = definitions?.agent.projection ?? "";
  const diagnosisContract = (
    contract?.contract as
      | { diagnosis?: { providers?: string[]; default_model?: string } }
      | undefined
  )?.diagnosis;
  const providers = diagnosisContract?.providers ?? ["openrouter", "stub"];
  const projectionRuns = trace?.projection_runs ?? [];
  const lastProjectionRun = projectionRuns.at(-1);

  const runStage = async <R extends { ok: boolean; diagnostics?: Diagnostic[] }>(
    stage: string,
    call: () => Promise<R>,
    apply: (r: StageResult<R>) => void,
    /** Non-ok results that still persisted store state (a partial
     * ingest's accepted rows, a recorded run) must refresh the
     * overview too — the banner and last-run lines read the store. */
    persisted?: (r: R) => boolean,
  ) => {
    const activePath = path;
    setPendingStage(stage);
    try {
      const response = await call();
      if (pathRef.current !== activePath) return;
      if (response.ok) {
        apply({ kind: "done", response });
        refresh();
      } else {
        apply({ kind: "domain", diagnostics: response.diagnostics ?? [] });
        if (persisted?.(response)) refresh();
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      if (pathRef.current !== activePath) return;
      apply({ kind: "transport", error: toError(err) });
    } finally {
      setPendingStage(null);
    }
  };

  if (!path) return null;

  const submitIngest = () =>
    runStage("ingest", () =>
      api.ingest(path, {
        text: snapshotText,
        name: snapshotName.trim() || undefined,
        source:
          sources.length > 1
            ? snapshotSource || undefined
            : sources[0]?.name,
      }),
      setIngestResult,
      (r) => (r.accepted ?? 0) > 0,
    );

  const submitProject = () =>
    runStage("project", () =>
      api.project(
        path,
        asOf.trim(),
        projectionName || undefined,
      ),
      setProjectResult,
      (r) => r.recorded === true,
    );

  const submitDiagnose = () =>
    runStage("diagnose", () =>
      api.diagnose(
        path,
        diagSource === "run"
          ? { runId: lastProjectionRun?.run_id }
          : { asOf: diagAsOf.trim() },
        { provider, model: model.trim() || undefined },
      ),
      setDiagnoseResult,
      (r) => r.recorded === true,
    );

  const submitEvaluate = () =>
    runStage("evaluate", () =>
      api.evaluate(path, boundary.trim(), evalAsOf.trim()),
      setEvaluateResult,
      (r) => r.recorded === true,
    );

  const stageMarker = (stage: LoopPosition["stage"]) =>
    position.stage === stage ? (
      <span className="run-next-badge" role="status">
        next
      </span>
    ) : null;

  return (
    <div className="run-page">
      <h1>Run the loop</h1>
      <p className="hint">
        The five stages in order — ingest → project → diagnose →
        review/execute → evaluate. Each trigger runs the same shared
        core as its CLI command and writes to this workspace's
        <code>.ontology/</code>. Inspect anything on{" "}
        <Link to="/workspace/trace">Trace</Link>,{" "}
        <Link to="/workspace/proposals">Proposals</Link>, or{" "}
        <Link to="/workspace/reports">Reports</Link>.
      </p>

      <section
        className="run-next"
        aria-label="next available action"
        aria-live="polite"
      >
        {overview.status === "loading" ? (
          <LoadingState label="Reading the loop state…" />
        ) : overview.status === "failed" ? (
          <ErrorState
            error={overview.error}
            onRetry={
              overview.error instanceof ApiError && overview.error.retryable
                ? refresh
                : undefined
            }
          />
        ) : (
          <>
            <p>
              <strong>Next:</strong> {position.label}
              {position.to ? (
                <>
                  {" "}
                  — <Link to={position.to}>open</Link>
                </>
              ) : null}
            </p>
            <p className="hint">{position.detail}</p>
            <div className="add-row">
              <button
                type="button"
                disabled={refreshing}
                onClick={refresh}
              >
                {refreshing ? "Refreshing…" : "Refresh state"}
              </button>
            </div>
          </>
        )}
      </section>

      {trace?.diagnostics?.length ? (
        <DiagnosticsList diagnostics={trace.diagnostics} />
      ) : null}

      <ol className="run-stages" aria-label="loop stages">
        <li
          className="run-stage"
          aria-label="stage 1 ingest"
          data-next={position.stage === "ingest" || undefined}
        >
          <h2>
            1. Ingest {stageMarker("ingest")}
          </h2>
          <p className="hint">
            Last run:{" "}
            {trace?.evidence?.ingest_runs?.length ? (
              <code>{trace.evidence.ingest_runs.at(-1)}</code>
            ) : (
              "none recorded"
            )}
          </p>
          <div className="form-field">
            <label htmlFor="run-snapshot-text">Snapshot text</label>
            <textarea
              id="run-snapshot-text"
              rows={6}
              value={snapshotText}
              onChange={(e) => setSnapshotText(e.target.value)}
              placeholder="Paste the snapshot exactly as the source produced it — format follows the declared source mapping."
            />
          </div>
          <div className="form-field">
            <label htmlFor="run-snapshot-name">Name (optional)</label>
            <input
              id="run-snapshot-name"
              type="text"
              value={snapshotName}
              onChange={(e) => setSnapshotName(e.target.value)}
              placeholder="e.g. jobs-current.csv — a label only; the API sanitizes it to a basename"
            />
          </div>
          {sources.length > 1 ? (
            <div className="form-field">
              <label htmlFor="run-snapshot-source">Source</label>
              <select
                id="run-snapshot-source"
                value={snapshotSource}
                onChange={(e) => setSnapshotSource(e.target.value)}
              >
                <option value="">— choose a declared source —</option>
                {sources.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name} ({s.format})
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          <div className="add-row">
            <button
              type="button"
              disabled={
                pendingStage !== null ||
                snapshotText === "" ||
                (sources.length > 1 && snapshotSource === "")
              }
              onClick={() => void submitIngest()}
            >
              {pendingStage === "ingest" ? "Ingesting…" : "Ingest snapshot"}
            </button>
            <p className="hint">
              Same bytes, same ingest — a re-submitted snapshot re-observes
              events instead of duplicating them.
            </p>
          </div>
          <ResultBlock
            result={ingestResult}
            onRetry={() => void submitIngest()}
          >
            {(r) => (
              <p>
                ingest <code>{r.ingest_id}</code>: {r.accepted} accepted,{" "}
                {r.rejected} rejected
                {r.canonical ? (
                  <>
                    {" "}
                    — {r.canonical.events_appended} events appended,{" "}
                    {r.canonical.events_reobserved} re-observed
                  </>
                ) : null}
                {r.evidence?.observations ? (
                  <>
                    {" "}
                    — <Link to="/workspace/trace">evidence</Link>
                  </>
                ) : null}
              </p>
            )}
          </ResultBlock>
        </li>

        <li
          className="run-stage"
          aria-label="stage 2 project"
          data-next={position.stage === "project" || undefined}
        >
          <h2>
            2. Project {stageMarker("project")}
          </h2>
          <p className="hint">
            Last run:{" "}
            {lastProjectionRun ? (
              <>
                <code>{lastProjectionRun.run_id}</code> —{" "}
                {lastProjectionRun.candidates} candidate(s) as of{" "}
                <code>{lastProjectionRun.as_of}</code>
              </>
            ) : (
              "none recorded"
            )}
          </p>
          <div className="form-field">
            <label htmlFor="run-as-of">Projection as-of (required)</label>
            <input
              id="run-as-of"
              type="text"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              placeholder="2026-10-05T00:00:00Z — ISO 8601 with an explicit offset"
            />
          </div>
          {projections.length ? (
            <div className="form-field">
              <label htmlFor="run-projection">Projection</label>
              <select
                id="run-projection"
                value={projectionName}
                onChange={(e) => setProjectionName(e.target.value)}
              >
                <option value="">
                  {defaultProjection || "default"} (default)
                </option>
                {projections.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          <div className="add-row">
            <button
              type="button"
              disabled={pendingStage !== null || asOf.trim() === ""}
              onClick={() => void submitProject()}
            >
              {pendingStage === "project" ? "Projecting…" : "Run projection"}
            </button>
          </div>
          <ResultBlock
            result={projectResult}
            onRetry={() => void submitProject()}
          >
            {(r) => (
              <p>
                run <code>{r.run_id}</code>: {r.candidates?.length ?? 0}{" "}
                candidate(s)
                {r.candidates?.length ? (
                  <>
                    {" "}
                    —{" "}
                    {r.candidates
                      .map(
                        (c) => `${c.subject.entity}/${c.subject.id}`,
                      )
                      .join(", ")}
                  </>
                ) : null}
              </p>
            )}
          </ResultBlock>
        </li>

        <li
          className="run-stage"
          aria-label="stage 3 diagnose"
          data-next={position.stage === "diagnose" || undefined}
        >
          <h2>
            3. Diagnose {stageMarker("diagnose")}
          </h2>
          <p className="hint">
            Last run:{" "}
            {trace?.diagnosis_runs?.length ? (
              <>
                <code>{trace.diagnosis_runs.at(-1)?.run_id}</code> —{" "}
                {trace.diagnosis_runs.at(-1)?.provider}/
                {trace.diagnosis_runs.at(-1)?.model}{" "}
                <StatusBadge
                  status={trace.diagnosis_runs.at(-1)?.status ?? null}
                />
              </>
            ) : (
              "none recorded"
            )}
          </p>
          {lastProjectionRun && lastProjectionRun.candidates === 0 ? (
            <p className="hint" role="note">
              The last projection selected no candidates — a diagnosis now
              would find nothing. Adjust the as-of or ingest more evidence.
            </p>
          ) : null}
          <div className="form-field">
            <label htmlFor="run-diag-source">Candidate source</label>
            <select
              id="run-diag-source"
              value={diagSource}
              onChange={(e) =>
                setDiagSource(e.target.value as "run" | "as_of")
              }
            >
              <option value="run" disabled={!lastProjectionRun}>
                {lastProjectionRun
                  ? `latest projection run (${lastProjectionRun.run_id})`
                  : "latest projection run (none yet)"}
              </option>
              <option value="as_of">as-of timestamp</option>
            </select>
          </div>
          {diagSource === "as_of" ? (
            <div className="form-field">
              <label htmlFor="run-diag-as-of">As-of</label>
              <input
                id="run-diag-as-of"
                type="text"
                value={diagAsOf}
                onChange={(e) => setDiagAsOf(e.target.value)}
                placeholder="2026-10-05T00:00:00Z"
              />
            </div>
          ) : null}
          <div className="form-field">
            <label htmlFor="run-diag-provider">Provider</label>
            <select
              id="run-diag-provider"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
            >
              {providers.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            {provider === "openrouter" ? (
              <p className="hint">
                Requires OPENROUTER_API_KEY on the API host — use stub for
                a credential-free local run.
              </p>
            ) : null}
          </div>
          <div className="form-field">
            <label htmlFor="run-diag-model">Model (optional)</label>
            <input
              id="run-diag-model"
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={
                diagnosisContract?.default_model ?? "provider default"
              }
            />
          </div>
          <div className="add-row">
            <button
              type="button"
              disabled={
                pendingStage !== null ||
                (diagSource === "run" && !lastProjectionRun) ||
                (diagSource === "as_of" && diagAsOf.trim() === "")
              }
              onClick={() => void submitDiagnose()}
            >
              {pendingStage === "diagnose" ? "Diagnosing…" : "Run diagnosis"}
            </button>
            <p className="hint">
              Each run — including a retry after a busy store — records a
              new diagnosis run and calls the provider again.
            </p>
          </div>
          <ResultBlock
            result={diagnoseResult}
            onRetry={() => void submitDiagnose()}
          >
            {(r) => (
              <p>
                run <code>{r.run_id}</code> <StatusBadge status={r.status ?? null} />{" "}
                — {r.candidate_count} candidate(s),{" "}
                {r.proposals?.length ?? 0} proposal(s)
                {r.proposals?.length ? (
                  <>
                    {" "}
                    — <Link to="/workspace/proposals">review them</Link>
                  </>
                ) : null}
              </p>
            )}
          </ResultBlock>
        </li>

        <li
          className="run-stage"
          aria-label="stage 4 review and execute"
          data-next={
            position.stage === "review" || position.stage === "execute" ||
            undefined
          }
        >
          <h2>
            4. Review &amp; execute{" "}
            {stageMarker("review")}
            {stageMarker("execute")}
          </h2>
          <p className="hint">
            {trace?.proposals?.total ?? 0} proposal(s) —{" "}
            {Object.entries(trace?.proposals?.by_status ?? {})
              .map(([status, n]) => `${n} ${status}`)
              .join(", ") || "none"}
            ; {trace?.executions?.attempts ?? 0} execution attempt(s)
            recorded. Decisions are recorded on the{" "}
            <Link to="/workspace/proposals">Proposals</Link> page.
          </p>
          {proposals.filter((p) => p.status === "proposed").length ? (
            <p className="hint" role="note">
              Blocked: {proposals.filter((p) => p.status === "proposed").length}{" "}
              proposal(s) await a human decision — execution stays gated
              until approved.{" "}
              <Link to="/workspace/proposals">Review now</Link>.
            </p>
          ) : null}
          {proposals
            .filter(
              (p) =>
                p.status === "approved" &&
                !p.executions.some((e) => e.status === "succeeded"),
            )
            .map((p) => (
              <div key={p.proposal_id} className="run-execute-row">
                <code>{p.proposal_id}</code> — {p.action}
                <ExecuteControl
                  path={path}
                  proposalId={p.proposal_id}
                  onExecuted={refresh}
                />
              </div>
            ))}
          {!proposals.length ? (
            <EmptyState title="No proposals yet.">
              <p>A diagnosis run persists them — the previous stage.</p>
            </EmptyState>
          ) : null}
        </li>

        <li
          className="run-stage"
          aria-label="stage 5 evaluate"
          data-next={position.stage === "evaluate" || undefined}
        >
          <h2>
            5. Evaluate {stageMarker("evaluate")}
          </h2>
          <p className="hint">
            Last run:{" "}
            {trace?.evaluation_runs?.length ? (
              <>
                <code>{trace.evaluation_runs.at(-1)?.run_id}</code>{" "}
                <StatusBadge
                  status={trace.evaluation_runs.at(-1)?.overall ?? null}
                />
              </>
            ) : (
              "none recorded"
            )}
          </p>
          <div className="form-field">
            <label htmlFor="run-boundary">Boundary (required)</label>
            <input
              id="run-boundary"
              type="text"
              value={boundary}
              onChange={(e) => setBoundary(e.target.value)}
              placeholder="2026-10-15T00:00:00Z — the cohort split instant"
            />
          </div>
          <div className="form-field">
            <label htmlFor="run-eval-as-of">
              Evaluation as-of (required)
            </label>
            <input
              id="run-eval-as-of"
              type="text"
              value={evalAsOf}
              onChange={(e) => setEvalAsOf(e.target.value)}
              placeholder="2026-10-26T00:00:00Z"
            />
          </div>
          <div className="add-row">
            <button
              type="button"
              disabled={
                pendingStage !== null ||
                boundary.trim() === "" ||
                evalAsOf.trim() === ""
              }
              onClick={() => void submitEvaluate()}
            >
              {pendingStage === "evaluate" ? "Evaluating…" : "Run evaluation"}
            </button>
          </div>
          <ResultBlock
            result={evaluateResult}
            onRetry={() => void submitEvaluate()}
          >
            {(r) => (
              <p>
                run <code>{r.run_id}</code>{" "}
                <StatusBadge status={r.report?.overall?.status ?? null} />{" "}
                — <Link to="/workspace/reports">open the report</Link>
              </p>
            )}
          </ResultBlock>
        </li>
      </ol>
    </div>
  );
}
