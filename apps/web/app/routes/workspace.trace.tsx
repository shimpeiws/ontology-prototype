/**
 * Trace view (#62): follow a recorded run from evidence to outcome.
 *
 * Loads the `workspaces/trace` overview on mount, then offers the two
 * focused lookups — `trace/subject` (Entity/Id) and `trace/run` (any
 * artifact id). Inputs are trimmed and blank submissions skipped
 * client-side; everything else is sent verbatim and the server owns
 * TRACE.INVALID_SUBJECT / TRACE.UNKNOWN_ID, returned as domain
 * diagnostics which render through DiagnosticsList — never as
 * transport errors.
 */

import { useEffect, useId, useRef, useState } from "react";

import { api, ApiError } from "../lib/api";
import type {
  SubjectTrace,
  TraceAnchor,
  TraceOverviewResponse,
  TraceViewResponse,
} from "../lib/types";
import {
  AnchorTraceView,
  SubjectTraceView,
  TraceOverviewView,
} from "../components/TraceView";
import { DiagnosticsList } from "../components/DiagnosticsList";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { useWorkspace } from "../workspace/context";

export function meta() {
  return [{ title: "ontology — trace" }];
}

type OverviewState =
  | { status: "loading" }
  | { status: "failed"; error: ApiError | Error }
  | { status: "done"; response: TraceOverviewResponse };

type LookupKind = "subject" | "artifact";

/** One lookup and its outcome, keyed to the exact input that produced it. */
interface LookupState {
  kind: LookupKind;
  value: string;
  status: "running" | "done" | "failed";
  response?: TraceViewResponse;
  error?: ApiError | Error;
}

function toError(err: unknown): ApiError | Error {
  return err instanceof Error ? err : new Error(String(err));
}

function isAnchorTrace(trace: SubjectTrace | TraceAnchor): trace is TraceAnchor {
  return "kind" in trace;
}

export default function TracePage() {
  const { state } = useWorkspace();
  const path = state.workspace?.requestedPath ?? null;
  const subjectId = useId();
  const artifactId = useId();

  const [overview, setOverview] = useState<OverviewState>({
    status: "loading",
  });
  const [overviewSeq, setOverviewSeq] = useState(0);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    setOverview({ status: "loading" });
    api
      .traceOverview(path, controller.signal)
      .then((response) => setOverview({ status: "done", response }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setOverview({ status: "failed", error: toError(err) });
      });
    return () => controller.abort();
  }, [path, overviewSeq]);

  const [subjectInput, setSubjectInput] = useState("");
  const [artifactInput, setArtifactInput] = useState("");
  const [lookup, setLookup] = useState<LookupState | null>(null);
  // Single-flight: a superseded request's response is discarded, never
  // rendered as the answer to the newer query.
  const lookupSeq = useRef(0);

  const runLookup = async (kind: LookupKind, value: string) => {
    const trimmed = value.trim();
    if (!path || !trimmed) return;
    const seq = ++lookupSeq.current;
    setLookup({ kind, value: trimmed, status: "running" });
    try {
      const response =
        kind === "subject"
          ? await api.traceSubject(path, trimmed)
          : await api.traceRun(path, trimmed);
      if (seq !== lookupSeq.current) return;
      setLookup({ kind, value: trimmed, status: "done", response });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      if (seq !== lookupSeq.current) return;
      setLookup({
        kind,
        value: trimmed,
        status: "failed",
        error: toError(err),
      });
    }
  };

  const followSubject = (subject: string) => {
    setSubjectInput(subject);
    void runLookup("subject", subject);
  };

  // A shown result answers exactly the input it was submitted with —
  // editing the field afterwards makes the result stale, not wrong.
  const lookupStale =
    lookup !== null &&
    lookup.status !== "running" &&
    (lookup.kind === "subject"
      ? lookup.value !== subjectInput.trim()
      : lookup.value !== artifactInput.trim());

  return (
    <div className="trace-page">
      <h1>Trace</h1>
      <p className="hint">
        The recorded run of this workspace — observation evidence through
        evaluation reports — read-only via the API. Follow one subject or
        inspect a single artifact id.
      </p>

      <section className="trace-lookup" aria-label="trace lookups">
        <div className="form-field">
          <label htmlFor={subjectId}>Subject</label>
          <div className="add-row">
            <input
              id={subjectId}
              type="text"
              value={subjectInput}
              placeholder="Job/101"
              onChange={(e) => setSubjectInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void runLookup("subject", subjectInput);
              }}
            />
            <button
              type="button"
              disabled={!path || !subjectInput.trim() || lookup?.status === "running"}
              onClick={() => void runLookup("subject", subjectInput)}
            >
              Follow subject
            </button>
          </div>
        </div>
        <div className="form-field">
          <label htmlFor={artifactId}>Artifact id</label>
          <div className="add-row">
            <input
              id={artifactId}
              type="text"
              value={artifactInput}
              placeholder="dgx-… / evl-… / obs-…"
              onChange={(e) => setArtifactInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void runLookup("artifact", artifactInput);
              }}
            />
            <button
              type="button"
              disabled={
                !path || !artifactInput.trim() || lookup?.status === "running"
              }
              onClick={() => void runLookup("artifact", artifactInput)}
            >
              Inspect artifact
            </button>
          </div>
        </div>
      </section>

      <div aria-live="polite">
        {lookupStale ? (
          <p className="field-diagnostics stale" role="status">
            <span className="badge badge-stale">stale</span> The input changed
            since this lookup ran — run it again for current results.
          </p>
        ) : null}
        {lookup?.status === "running" ? (
          <LoadingState
            label={
              lookup.kind === "subject"
                ? `Following ${lookup.value}…`
                : `Inspecting ${lookup.value}…`
            }
          />
        ) : lookup?.status === "failed" && lookup.error ? (
          <ErrorState
            error={lookup.error}
            onRetry={
              lookup.error instanceof ApiError && lookup.error.retryable
                ? () => void runLookup(lookup.kind, lookup.value)
                : undefined
            }
          />
        ) : lookup?.status === "done" && lookup.response ? (
          !lookup.response.ok || !lookup.response.trace ? (
            <>
              <EmptyState title="Nothing found for that query." />
              <DiagnosticsList
                diagnostics={lookup.response.diagnostics ?? []}
              />
            </>
          ) : (
            <>
              {/* `ok` only reflects error-severity diagnostics — warnings
                  (e.g. TRACE.JOURNAL_CORRUPT) accompany a good trace and
                  must render as evidence too. */}
              {lookup.response.diagnostics?.length ? (
                <DiagnosticsList diagnostics={lookup.response.diagnostics} />
              ) : null}
              {isAnchorTrace(lookup.response.trace) ? (
                <AnchorTraceView
                  trace={lookup.response.trace}
                  onFollowSubject={followSubject}
                />
              ) : (
                <SubjectTraceView trace={lookup.response.trace} />
              )}
            </>
          )
        ) : null}
      </div>

      <section aria-label="workspace trace overview">
        <h2>Overview</h2>
        {overview.status === "loading" ? (
          <LoadingState label="Loading trace overview…" />
        ) : overview.status === "failed" ? (
          <ErrorState
            error={overview.error}
            onRetry={
              overview.error instanceof ApiError && overview.error.retryable
                ? () => setOverviewSeq((n) => n + 1)
                : undefined
            }
          />
        ) : (
          <TraceOverviewView overview={overview.response} />
        )}
      </section>
    </div>
  );
}
