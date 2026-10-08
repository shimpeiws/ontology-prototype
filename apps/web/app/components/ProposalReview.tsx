/**
 * Proposal review card (#63): the operational console's decision surface
 * for one persisted proposal.
 *
 * The card renders the exact persisted record — action, params, payload
 * digest, grounding evidence — so the reviewer decides on the same bytes
 * the approval binds to (`payload_digest`). The proposal's `reason` is
 * AI output recorded at diagnosis time: it is labeled a hypothesis and
 * never presented as fact. Execution history is status only — ADR 0003
 * keeps run triggers and write-back on the CLI; there is no execute
 * affordance here.
 */

import { useRef, useState } from "react";

import { api, ApiError } from "../lib/api";
import type { DecisionResponse, Diagnostic, ProposalView } from "../lib/types";
import { DiagnosticsList } from "./DiagnosticsList";
import { EmptyState, ErrorState } from "./states";
import { NatureBadge, StatusBadge } from "./StatusBadge";
import { RecordDetails } from "./TraceView";

function toError(err: unknown): ApiError | Error {
  return err instanceof Error ? err : new Error(String(err));
}

type DecideOutcome =
  | { kind: "domain"; decision: string; diagnostics: Diagnostic[] }
  | {
      kind: "transport";
      decision: "approved" | "rejected";
      error: ApiError | Error;
    }
  | { kind: "recorded"; response: DecisionResponse };

export function ProposalReviewCard({
  path,
  proposal,
  reviewer,
  onDecided,
}: {
  path: string;
  proposal: ProposalView;
  /** Self-asserted reviewer identity (ADR 0003), shared page-level. */
  reviewer: string;
  /** Called after a decision is recorded so the list can refresh. */
  onDecided: () => void;
}) {
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<"approved" | "rejected" | null>(
    null,
  );
  const [outcome, setOutcome] = useState<DecideOutcome | null>(null);
  // Single-flight per card: a superseded submission's late response is
  // discarded — never rendered as the answer to the newer attempt.
  const seq = useRef(0);

  const submit = async (decision: "approved" | "rejected") => {
    const attempt = ++seq.current;
    setPending(decision);
    setOutcome(null);
    try {
      const response = await api.decide(
        path,
        proposal.proposal_id,
        decision,
        reviewer.trim(),
        reason.trim() || null,
      );
      if (attempt !== seq.current) return;
      if (response.ok && response.recorded) {
        setOutcome({ kind: "recorded", response });
        setReason("");
        onDecided();
      } else {
        setOutcome({
          kind: "domain",
          decision,
          diagnostics: response.diagnostics ?? [],
        });
      }
    } catch (err) {
      if (
        err instanceof DOMException &&
        err.name === "AbortError"
      ) {
        return;
      }
      if (attempt !== seq.current) return;
      setOutcome({ kind: "transport", decision, error: toError(err) });
    } finally {
      if (attempt === seq.current) setPending(null);
    }
  };

  const reviewerBlank = reviewer.trim() === "";

  return (
    <article className="proposal-card" data-status={proposal.status}>
      <header className="proposal-head">
        <h3>
          <code>{proposal.proposal_id}</code>{" "}
          <StatusBadge status={proposal.status} />
        </h3>
      </header>

      <dl className="definition-facts">
        <dt>Action</dt>
        <dd>
          <code>{proposal.action}</code>
        </dd>
        <dt>Payload digest</dt>
        <dd>
          <code>{proposal.payload_digest}</code>
        </dd>
        {proposal.subject_entity || proposal.subject_id ? (
          <>
            <dt>Canonical target</dt>
            <dd>
              <code>
                {proposal.subject_entity}/{proposal.subject_id}
              </code>
            </dd>
          </>
        ) : null}
        {proposal.related_entities?.length ? (
          <>
            <dt>Related entities</dt>
            <dd>
              {proposal.related_entities.map((e) => (
                <code key={`${e.entity}/${e.id}`} className="id-chip">
                  {e.entity}/{e.id}
                </code>
              ))}
            </dd>
          </>
        ) : null}
        <dt>Diagnosis run</dt>
        <dd>
          <code>{proposal.diagnosis_run_id}</code>
        </dd>
        <dt>Proposed at</dt>
        <dd>
          <code>{proposal.created_at}</code>
        </dd>
      </dl>

      <section className="proposal-payload" aria-label="exact payload">
        <h4>Exact payload</h4>
        <p className="hint">
          The approval binds to the digest of exactly these fields — a
          changed payload fails the execution gate.
        </p>
        <RecordDetails record={proposal.params} />
      </section>

      <section className="proposal-rationale" aria-label="AI rationale">
        <h4>
          AI rationale <NatureBadge nature="ai" />
        </h4>
        <p className="hint">
          Model output recorded at diagnosis time — a hypothesis for the
          reviewer to weigh, not a verified fact.
        </p>
        {proposal.reason ? (
          <p className="proposal-reason">{proposal.reason}</p>
        ) : (
          <p className="hint">No rationale was recorded.</p>
        )}
        <details className="record-json">
          <summary>grounding evidence</summary>
          <pre>{JSON.stringify(proposal.evidence, null, 2)}</pre>
        </details>
      </section>

      <section className="proposal-decisions" aria-label="decision history">
        <h4>
          Decisions <NatureBadge nature="human" />
        </h4>
        {proposal.decisions.length ? (
          <ul className="trace-list">
            {proposal.decisions.map((d) => (
              <li key={d.decision_id}>
                <code>{d.decision_id}</code>{" "}
                <StatusBadge status={d.decision} /> by {d.reviewer}{" "}
                <span className="hint">{d.decided_at}</span>
                {d.reason ? <p className="hint">{d.reason}</p> : null}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No human decisions recorded." />
        )}
      </section>

      <section
        className="proposal-executions"
        aria-label="execution history"
      >
        <h4>
          Executions <NatureBadge nature="writeback" />
        </h4>
        <p className="hint">
          Recorded attempts only — execution runs through{" "}
          <code>ontology execute</code>, never from this console.
        </p>
        {proposal.executions.length ? (
          <ul className="trace-list">
            {proposal.executions.map((attempt) => (
              <li key={attempt.execution_id}>
                <code>{attempt.execution_id}</code> —{" "}
                <code>{attempt.action}</code> via {attempt.adapter}{" "}
                attempt {attempt.attempt}{" "}
                <StatusBadge status={attempt.status} />
                {attempt.error ? (
                  <p className="hint">error: {attempt.error}</p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No write-back attempts recorded." />
        )}
      </section>

      <section className="proposal-decide" aria-label="record a decision">
        <h4>Record decision</h4>
        <div className="form-field">
          <label htmlFor={`reason-${proposal.proposal_id}`}>
            Reason (optional)
          </label>
          <input
            id={`reason-${proposal.proposal_id}`}
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why this decision — recorded with it"
          />
        </div>
        <div className="add-row">
          <button
            type="button"
            disabled={pending !== null || reviewerBlank}
            title={
              reviewerBlank
                ? "A reviewer identity is required — every decision records who made it."
                : undefined
            }
            onClick={() => void submit("approved")}
          >
            {pending === "approved" ? "Recording…" : "Approve"}
          </button>
          <button
            type="button"
            className="danger"
            disabled={pending !== null || reviewerBlank}
            title={
              reviewerBlank
                ? "A reviewer identity is required — every decision records who made it."
                : undefined
            }
            onClick={() => void submit("rejected")}
          >
            {pending === "rejected" ? "Recording…" : "Reject"}
          </button>
        </div>
        {outcome?.kind === "recorded" ? (
          <p className="state state-saved" role="status">
            {outcome.response.decision} recorded as{" "}
            <code>{outcome.response.decision_id}</code>
          </p>
        ) : null}
        {outcome?.kind === "domain" ? (
          <DiagnosticsList diagnostics={outcome.diagnostics} />
        ) : null}
        {outcome?.kind === "transport" ? (
          <ErrorState
            error={outcome.error}
            onRetry={
              outcome.error instanceof ApiError && outcome.error.retryable
                ? () => void submit(outcome.decision)
                : undefined
            }
          />
        ) : null}
      </section>
    </article>
  );
}
