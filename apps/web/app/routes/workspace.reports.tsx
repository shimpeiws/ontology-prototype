/**
 * Evaluation reports view (#62): the workspace's frozen outcome reports.
 *
 * `workspaces/evaluations` lists every frozen run; selecting one fetches
 * the frozen report via `workspaces/evaluations/run` (the `evaluate
 * --run` payload). Verdicts render through the shared status badges —
 * `not_met`/`unverified` never look like success and `unavailable`
 * guardrails are visually distinct from `passed`.
 */

import { useEffect, useRef, useState } from "react";
import { Button } from "antd";

import { api, ApiError } from "../lib/api";
import type {
  EvaluationRunRecord,
  EvaluationRunResponse,
  EvaluationRunsResponse,
} from "../lib/types";
import { EvaluationReportView } from "../components/EvaluationReport";
import { DiagnosticsList } from "../components/DiagnosticsList";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspace } from "../workspace/context";

export function meta() {
  return [{ title: "ontology — evaluation reports" }];
}

type ListState =
  | { status: "loading" }
  | { status: "failed"; error: ApiError | Error }
  | { status: "done"; response: EvaluationRunsResponse };

/** One report fetch and its outcome, keyed to the run id it asked for. */
interface DetailState {
  runId: string;
  status: "running" | "done" | "failed";
  response?: EvaluationRunResponse;
  error?: ApiError | Error;
}

function toError(err: unknown): ApiError | Error {
  return err instanceof Error ? err : new Error(String(err));
}

function RunRow({
  run,
  selected,
  onSelect,
}: {
  run: EvaluationRunRecord;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <Button
        type="link"
        className={`link-button${selected ? " report-selected" : ""}`}
        aria-pressed={selected}
        onClick={onSelect}
      >
        <code>{run.run_id}</code>
      </Button>{" "}
      — {run.evaluation} as of <code>{run.as_of}</code>{" "}
      <StatusBadge status={run.report?.overall?.status ?? null} />
    </li>
  );
}

export default function ReportsPage() {
  const { state } = useWorkspace();
  const path = state.workspace?.requestedPath ?? null;

  const [list, setList] = useState<ListState>({ status: "loading" });
  const [listSeq, setListSeq] = useState(0);
  const [detail, setDetail] = useState<DetailState | null>(null);
  // Single-flight: a superseded detail request (same or different run id)
  // is discarded, never rendered over the newer selection.
  const detailSeq = useRef(0);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    detailSeq.current += 1;
    setList({ status: "loading" });
    setDetail(null);
    api
      .evaluationRuns(path, controller.signal)
      .then((response) => setList({ status: "done", response }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setList({ status: "failed", error: toError(err) });
      });
    return () => controller.abort();
  }, [path, listSeq]);

  const openRun = async (runId: string) => {
    if (!path) return;
    const seq = ++detailSeq.current;
    setDetail({ runId, status: "running" });
    try {
      const response = await api.evaluationRun(path, runId);
      if (seq !== detailSeq.current) return;
      setDetail({ runId, status: "done", response });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      if (seq !== detailSeq.current) return;
      setDetail({ runId, status: "failed", error: toError(err) });
    }
  };

  const runs =
    list.status === "done" ? (list.response.evaluation_runs ?? []) : [];

  return (
    <div className="reports-page">
      <h1>Evaluation reports</h1>
      <p className="hint">
        Frozen outcome reports recorded by{" "}
        <code>ontology evaluate</code> — read-only; reports are never
        recomputed here.
      </p>

      {list.status === "loading" ? (
        <LoadingState label="Loading evaluation runs…" />
      ) : list.status === "failed" ? (
        <ErrorState
          error={list.error}
          onRetry={
            list.error instanceof ApiError && list.error.retryable
              ? () => setListSeq((n) => n + 1)
              : undefined
          }
        />
      ) : !list.response.ok ? (
        <DiagnosticsList diagnostics={list.response.diagnostics ?? []} />
      ) : runs.length === 0 ? (
        <EmptyState title="No frozen evaluation reports.">
          <p>
            Run <code>ontology evaluate --boundary … --as-of …</code> to
            record one.
          </p>
        </EmptyState>
      ) : (
        <ul className="run-list report-list">
          {runs.map((run) => (
            <RunRow
              key={run.run_id}
              run={run}
              selected={detail?.runId === run.run_id}
              onSelect={() => void openRun(run.run_id)}
            />
          ))}
        </ul>
      )}

      <div aria-live="polite">
        {detail?.status === "running" ? (
          <LoadingState label={`Loading report ${detail.runId}…`} />
        ) : detail?.status === "failed" && detail.error ? (
          <ErrorState
            error={detail.error}
            onRetry={
              detail.error instanceof ApiError && detail.error.retryable
                ? () => void openRun(detail.runId)
                : undefined
            }
          />
        ) : detail?.status === "done" && detail.response ? (
          !detail.response.ok || !detail.response.run ? (
            <>
              <EmptyState title="That report is not available." />
              <DiagnosticsList
                diagnostics={detail.response.diagnostics ?? []}
              />
            </>
          ) : (
            <EvaluationReportView run={detail.response.run} />
          )
        ) : null}
      </div>
    </div>
  );
}
