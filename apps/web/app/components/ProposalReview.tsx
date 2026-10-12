/**
 * Proposal review card (#63): the operational console's decision surface
 * for one persisted proposal.
 *
 * The card renders the exact persisted record — action, params, payload
 * digest, grounding evidence — so the reviewer decides on the same bytes
 * the approval binds to (`payload_digest`). The proposal's `reason` is
 * AI output recorded at diagnosis time: it is labeled a hypothesis and
 * never presented as fact. An approved proposal can be executed from
 * this card (ADR 0004): the armed confirm runs the same core gate the
 * CLI enforces, and the receipt — or the refusal diagnostics — renders
 * in place.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Alert, Button, Input, Tag } from "antd";

import { api, ApiError } from "../lib/api";
import type {
  DecisionResponse,
  Diagnostic,
  DiagnosisRunRecord,
  ProposalView,
  TraceLink,
} from "../lib/types";
import { DiagnosisDeliverables } from "./DiagnosisDeliverables";
import { DiagnosticsList } from "./DiagnosticsList";
import { EvidenceReferences } from "./EvidenceReferences";
import { ExecuteControl } from "./ExecuteControl";
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

/** Read-back of the proposing diagnosis run plus the proposal's
 *  recorded trace links — provenance for the card (#96/#97). */
type Provenance =
  | { status: "loading" }
  | { status: "failed"; error: ApiError | Error }
  | {
      status: "done";
      run: DiagnosisRunRecord | null;
      runDiagnostics: Diagnostic[];
      links: TraceLink[];
    };

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

  // Provenance read-back (#96/#97): the diagnosis run that produced the
  // proposal and the trace links that resolve its evidence ids. One
  // flight per card; a superseded response is never rendered.
  const [provenance, setProvenance] = useState<Provenance>({
    status: "loading",
  });
  const provSeq = useRef(0);
  useEffect(() => {
    const request = ++provSeq.current;
    const controller = new AbortController();
    setProvenance({ status: "loading" });
    Promise.all([
      api.diagnosisRun(path, proposal.diagnosis_run_id, controller.signal),
      api.traceRun(path, proposal.proposal_id, controller.signal),
    ])
      .then(([runResponse, anchorResponse]) => {
        if (controller.signal.aborted || request !== provSeq.current)
          return;
        const anchor = anchorResponse.trace;
        const links =
          anchorResponse.ok && anchor && "links" in anchor
            ? anchor.links
            : [];
        setProvenance({
          status: "done",
          run: runResponse.ok ? (runResponse.run ?? null) : null,
          runDiagnostics: runResponse.ok
            ? []
            : (runResponse.diagnostics ?? []),
          links,
        });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted || request !== provSeq.current)
          return;
        setProvenance({ status: "failed", error: toError(err) });
      });
    return () => controller.abort();
  }, [path, proposal.diagnosis_run_id, proposal.proposal_id]);

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

      <section
        className="proposal-provenance"
        aria-label="proposing diagnosis run"
      >
        <h4>
          Proposing diagnosis run <NatureBadge nature="ai" />
        </h4>
        {provenance.status === "loading" ? (
          <p className="hint">Loading the recorded run…</p>
        ) : provenance.status === "failed" ? (
          <p className="hint" role="status">
            The recorded run could not be loaded:{" "}
            {provenance.error.message}
          </p>
        ) : provenance.run ? (
          <>
            <p>
              <Link
                to={`/workspace/trace?artifact=${encodeURIComponent(proposal.diagnosis_run_id)}`}
              >
                <code>{proposal.diagnosis_run_id}</code>
              </Link>{" "}
              — {provenance.run.provider}/{provenance.run.model}{" "}
              {provenance.run.provider === "stub" ? (
                <Tag className="badge" color="warning">
                  stub — deterministic test double, not a live model
                </Tag>
              ) : null}
            </p>
            <DiagnosisDeliverables
              deliverables={provenance.run.deliverables}
            />
          </>
        ) : (
          <>
            <p className="hint">
              The recorded run is no longer readable — the diagnostics
              explain why.
            </p>
            <DiagnosticsList diagnostics={provenance.runDiagnostics} />
          </>
        )}
      </section>

      <section className="proposal-payload" aria-label="exact payload">
        <h4>Exact payload</h4>
        <p className="hint">
          The approval binds to the digest of exactly these fields — a
          changed payload fails the execution gate.
          {"reason" in proposal.params
            ? " A `reason` key here is the action's own payload argument, not the model's rationale below."
            : ""}
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
      </section>

      <section className="proposal-evidence" aria-label="grounding evidence">
        <h4>
          Grounding evidence <NatureBadge nature="observed" />
        </h4>
        <p className="hint">
          The recorded references the diagnosis cited — each id opens the
          corresponding artifact on the Trace screen.
        </p>
        <EvidenceReferences
          evidence={proposal.evidence}
          links={
            provenance.status === "done" ? provenance.links : undefined
          }
          fromId={proposal.proposal_id}
          subject={{
            entity: proposal.subject_entity,
            id: proposal.subject_id,
          }}
        />
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
          Recorded write-back attempts — triggered from the CLI or from
          this console; an idempotent replay appends nothing.
        </p>
        {proposal.executions.length ? (
          <ul className="trace-list">
            {proposal.executions.map((attempt) => (
              // Each adapter-reaching attempt writes an `attempting`
              // intent row plus an outcome row — both share
              // `execution_id`, so the key needs attempt and status to
              // stay unique across the pair.
              <li
                key={`${attempt.execution_id}-${attempt.attempt}-${attempt.status}`}
              >
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

      <section className="proposal-execute" aria-label="execute proposal">
        <h4>
          Execute <NatureBadge nature="writeback" />
        </h4>
        <ExecuteControl
          path={path}
          proposalId={proposal.proposal_id}
          disabled={proposal.status !== "approved"}
          disabledReason="Execution requires an approved decision on this exact payload — the core enforces the gate."
          onExecuted={onDecided}
        />
      </section>

      <section className="proposal-decide" aria-label="record a decision">
        <h4>Record decision</h4>
        <div className="form-field">
          <label htmlFor={`reason-${proposal.proposal_id}`}>
            Reason (optional)
          </label>
          <Input
            id={`reason-${proposal.proposal_id}`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why this decision — recorded with it"
          />
        </div>
        <div className="add-row">
          <Button
            type="primary"
            disabled={pending !== null || reviewerBlank}
            title={
              reviewerBlank
                ? "A reviewer identity is required — every decision records who made it."
                : undefined
            }
            onClick={() => void submit("approved")}
          >
            {pending === "approved" ? "Recording…" : "Approve"}
          </Button>
          <Button
            danger
            disabled={pending !== null || reviewerBlank}
            title={
              reviewerBlank
                ? "A reviewer identity is required — every decision records who made it."
                : undefined
            }
            onClick={() => void submit("rejected")}
          >
            {pending === "rejected" ? "Recording…" : "Reject"}
          </Button>
        </div>
        {outcome?.kind === "recorded" ? (
          <Alert
            className="state state-saved"
            type="success"
            role="status"
            title={
              <>
                {outcome.response.decision} recorded as{" "}
                <code>{outcome.response.decision_id}</code>
              </>
            }
          />
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
