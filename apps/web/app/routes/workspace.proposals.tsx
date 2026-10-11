/**
 * Proposals view (#63): the operational console's review queue.
 *
 * Lists every persisted proposal with its exact payload, digest,
 * grounding evidence, and AI rationale — labeled as hypothesis, never
 * fact — and records the human approve/reject through `decide`
 * (ADR 0003). The decision binds the shown payload digest, so what the
 * reviewer inspected is exactly what execution may later run. An
 * approved proposal executes from its card under an armed
 * confirmation (ADR 0004) — the same core gate `ontology execute`
 * enforces.
 */

import { useEffect, useId, useRef, useState } from "react";
import { Button, Input } from "antd";

import { api, ApiError } from "../lib/api";
import type { ProposalsResponse } from "../lib/types";
import { DiagnosticsList } from "../components/DiagnosticsList";
import { ProposalReviewCard } from "../components/ProposalReview";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { useWorkspace } from "../workspace/context";

export function meta() {
  return [{ title: "ontology — proposals" }];
}

type ListState =
  | { status: "loading" }
  | { status: "failed"; error: ApiError | Error }
  | { status: "done"; response: ProposalsResponse };

function toError(err: unknown): ApiError | Error {
  return err instanceof Error ? err : new Error(String(err));
}

export default function ProposalsPage() {
  const { state } = useWorkspace();
  const path = state.workspace?.requestedPath ?? null;
  const reviewerId = useId();

  const [list, setList] = useState<ListState>({ status: "loading" });
  const [listSeq, setListSeq] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [reviewer, setReviewer] = useState("");
  // The workspace path the current list was fetched under. A same-path
  // refetch (manual refresh, a recorded decision) keeps the rendered
  // queue on screen; only a workspace switch returns to the spinner.
  const listPath = useRef<string | null>(null);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    const refetch = listPath.current === path;
    listPath.current = path;
    if (refetch) {
      setRefreshing(true);
    } else {
      setList({ status: "loading" });
    }
    api
      .proposals(path, controller.signal)
      .then((response) => setList({ status: "done", response }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setList({ status: "failed", error: toError(err) });
      })
      .finally(() => {
        if (!controller.signal.aborted) setRefreshing(false);
      });
    return () => {
      controller.abort();
      setRefreshing(false);
    };
  }, [path, listSeq]);

  const refresh = () => setListSeq((n) => n + 1);
  const proposals =
    list.status === "done" ? (list.response.proposals ?? []) : [];

  return (
    <div className="proposals-page">
      <h1>Proposals</h1>
      <p className="hint">
        Persisted action proposals awaiting or carrying a human decision.
        Each decision binds the exact payload digest shown and is recorded
        append-only under the reviewer identity below — latest decision
        wins. Rationale text is AI output: a hypothesis, not a verified
        fact. An approved proposal executes from its card after an
        explicit confirm — the same core gate as{" "}
        <code>ontology execute</code>.
      </p>

      <div className="form-field reviewer-field">
        <label htmlFor={reviewerId}>Reviewer identity</label>
        <Input
          id={reviewerId}
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="who records the decision (e.g. fde-sato)"
        />
        <p className="hint">
          Self-asserted in this local single-user setup (ADR 0003) —
          recorded verbatim on every decision you make here.
        </p>
      </div>

      <div className="add-row">
        <Button disabled={!path || refreshing} onClick={refresh}>
          {refreshing ? "Refreshing…" : "Refresh"}
        </Button>
        <p className="hint">
          This page does not watch the store — refresh to pick up
          proposals a CLI diagnosis run persisted since the page loaded.
        </p>
      </div>

      <section aria-label="proposals" aria-live="polite">
        {list.status === "loading" ? (
          <LoadingState label="Loading proposals…" />
        ) : list.status === "failed" ? (
          <ErrorState
            error={list.error}
            onRetry={
              list.error instanceof ApiError && list.error.retryable
                ? refresh
                : undefined
            }
          />
        ) : !list.response.ok || list.response.proposals === undefined ? (
          <>
            <EmptyState title="Proposals could not be loaded." />
            <DiagnosticsList
              diagnostics={list.response.diagnostics ?? []}
            />
          </>
        ) : (
          <>
            {list.response.diagnostics?.length ? (
              <DiagnosticsList
                diagnostics={list.response.diagnostics}
              />
            ) : null}
            {proposals.length ? (
              proposals.map((proposal) => (
                <ProposalReviewCard
                  key={proposal.proposal_id}
                  path={path as string}
                  proposal={proposal}
                  reviewer={reviewer}
                  onDecided={refresh}
                />
              ))
            ) : (
              <EmptyState title="No persisted proposals.">
                <p>
                  Proposals are persisted when a diagnosis run validates
                  them — run <code>ontology diagnose</code> to produce
                  candidates.
                </p>
              </EmptyState>
            )}
          </>
        )}
      </section>
    </div>
  );
}
