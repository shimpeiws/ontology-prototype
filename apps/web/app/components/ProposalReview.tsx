/**
 * Proposal review card (#63): the operational console's decision surface
 * for one persisted proposal.
 *
 * The card renders the exact persisted record — action, params, payload
 * digest, grounding evidence as inspectable references — so the reviewer
 * decides on the same bytes the approval binds to (`payload_digest`). The proposal's `reason` is
 * AI output recorded at diagnosis time: it is labeled a hypothesis and
 * never presented as fact. An approved proposal can be executed from
 * this card (ADR 0004): the armed confirm runs the same core gate the
 * CLI enforces, and the receipt — or the refusal diagnostics — renders
 * in place.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Alert, Button, Input } from "antd";

import { api, ApiError } from "../lib/api";
import type { DecisionResponse, Diagnostic, ProposalView } from "../lib/types";
import { DiagnosticsList } from "./DiagnosticsList";
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

/* Evidence fields rendered under their own labeled rows — ids as
 * traceable references, `entity_updated_at` as the candidate's as-of
 * stamp. Everything else recorded is passed through as context. */
const EVIDENCE_ID_KEYS = new Set([
  "fact_key",
  "observation_ids",
  "ingest_ids",
  "entity_observation_id",
  "entity_updated_at",
]);

function asId(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function asIdList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === "string" && v !== "")
    : [];
}

/** A non-id value still shown under its label — never dropped silently. */
function ScalarValue({ value }: { value: unknown }) {
  return value === null || value === undefined ? (
    <em className="preview-null">null</em>
  ) : (
    <code>{String(value)}</code>
  );
}

/**
 * One recorded reference — a deep link into the Trace screen
 * (`?run=<artifact id>` or `?subject=Entity/Id`). When the core's link
 * graph reports the target unresolved it stays shown, marked and
 * unlinked — a link into a lookup that can only say "nothing found"
 * would pretend navigability the console knows is absent.
 */
function EvidenceRef({
  id,
  kind,
  unresolved,
}: {
  id: string;
  kind: "run" | "subject";
  unresolved: Set<string> | null;
}) {
  if (unresolved?.has(id)) {
    return (
      <span className="link-unresolved">
        <code className="id-chip">{id}</code>{" "}
        <StatusBadge status="unresolved" label="unresolved" />
      </span>
    );
  }
  const param = kind === "subject" ? "subject" : "run";
  return (
    <Link to={`/workspace/trace?${param}=${encodeURIComponent(id)}`}>
      <code className="id-chip">{id}</code>
    </Link>
  );
}

function EvidenceRefList({
  ids,
  kind,
  unresolved,
}: {
  ids: string[];
  kind: "run" | "subject";
  unresolved: Set<string> | null;
}) {
  return (
    <>
      {ids.map((id, i) => (
        <EvidenceRef
          key={`${id}-${i}`}
          id={id}
          kind={kind}
          unresolved={unresolved}
        />
      ))}
    </>
  );
}

/** `dd` content for a single-id evidence field. */
function IdValue({
  value,
  unresolved,
}: {
  value: unknown;
  unresolved: Set<string> | null;
}) {
  const id = asId(value);
  return id !== null ? (
    <EvidenceRef id={id} kind="run" unresolved={unresolved} />
  ) : (
    <ScalarValue value={value} />
  );
}

/** `dd` content for an id-list evidence field. */
function IdListValue({
  value,
  unresolved,
}: {
  value: unknown;
  unresolved: Set<string> | null;
}) {
  if (!Array.isArray(value)) return <ScalarValue value={value} />;
  const ids = asIdList(value);
  return ids.length ? (
    <EvidenceRefList ids={ids} kind="run" unresolved={unresolved} />
  ) : (
    <em className="preview-null">none</em>
  );
}

/**
 * Grounding evidence (#97): the persisted evidence record as labeled,
 * inspectable references instead of a JSON dump. Each id deep-links to
 * the Trace screen; which targets resolve is the core's own verdict —
 * read from the proposal's trace anchor (`trace/run`), never guessed
 * client-side. A missing or failed anchor lookup leaves the references
 * rendered and navigable, just unmarked.
 */
function GroundingEvidence({
  unresolved,
  proposal,
}: {
  unresolved: Set<string> | null;
  proposal: ProposalView;
}) {
  const evidence = proposal.evidence;
  const context = Object.fromEntries(
    Object.entries(evidence).filter(([key]) => !EVIDENCE_ID_KEYS.has(key)),
  );
  const hasRefs = Object.keys(evidence).length > 0;

  return (
    <section className="proposal-evidence" aria-label="grounding evidence">
      <h4>
        Grounding evidence <NatureBadge nature="observed" />
      </h4>
      <p className="hint">
        Grounding recorded at diagnosis time — each id opens the matching
        artifact or subject on the Trace screen.
      </p>
      {hasRefs ? (
        <>
          <dl className="definition-facts">
            {"fact_key" in evidence ? (
              <>
                <dt>Fact</dt>
                <dd>
                  <IdValue
                    value={evidence.fact_key}
                    unresolved={unresolved}
                  />
                </dd>
              </>
            ) : null}
            {"observation_ids" in evidence ? (
              <>
                <dt>Observations</dt>
                <dd>
                  <IdListValue
                    value={evidence.observation_ids}
                    unresolved={unresolved}
                  />
                </dd>
              </>
            ) : null}
            {"ingest_ids" in evidence ? (
              <>
                <dt>Ingest runs</dt>
                <dd>
                  <IdListValue
                    value={evidence.ingest_ids}
                    unresolved={unresolved}
                  />
                </dd>
              </>
            ) : null}
            {"entity_observation_id" in evidence ? (
              <>
                <dt>Candidate entity observation</dt>
                <dd>
                  <IdValue
                    value={evidence.entity_observation_id}
                    unresolved={unresolved}
                  />
                </dd>
              </>
            ) : null}
            {"entity_updated_at" in evidence ? (
              <>
                <dt>Candidate state as of</dt>
                <dd>
                  <ScalarValue value={evidence.entity_updated_at} />
                </dd>
              </>
            ) : null}
          </dl>
          {Object.keys(context).length ? (
            <RecordDetails record={context} />
          ) : null}
        </>
      ) : (
        <EmptyState title="No grounding evidence was recorded." />
      )}
    </section>
  );
}

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
  // Which rendered references the core's link graph cannot resolve —
  // null until the proposal's trace anchor answers (or fails); refs
  // stay rendered and navigable either way.
  const [unresolved, setUnresolved] = useState<Set<string> | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .traceRun(path, proposal.proposal_id, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        const trace = response.trace;
        if (!response.ok || !trace) return;
        setUnresolved(
          new Set(
            (trace.unresolved ?? [])
              .filter((link) => link.from === proposal.proposal_id)
              .map((link) => link.to),
          ),
        );
      })
      .catch(() => {
        // Resolution marking is an enhancement — without it the
        // references stay rendered and navigable, and the Trace screen
        // still reports an id that resolves to nothing.
      });
    return () => controller.abort();
  }, [path, proposal.proposal_id]);

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
              {proposal.subject_entity && proposal.subject_id ? (
                <EvidenceRef
                  id={`${proposal.subject_entity}/${proposal.subject_id}`}
                  kind="subject"
                  unresolved={unresolved}
                />
              ) : (
                <code>
                  {proposal.subject_entity}/{proposal.subject_id}
                </code>
              )}
            </dd>
          </>
        ) : null}
        {proposal.related_entities?.length ? (
          <>
            <dt>Related entities</dt>
            <dd>
              {proposal.related_entities.map((e) => (
                <EvidenceRef
                  key={`${e.entity}/${e.id}`}
                  id={`${e.entity}/${e.id}`}
                  kind="subject"
                  unresolved={unresolved}
                />
              ))}
            </dd>
          </>
        ) : null}
        <dt>Diagnosis run</dt>
        <dd>
          <EvidenceRef
            id={proposal.diagnosis_run_id}
            kind="run"
            unresolved={unresolved}
          />
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
      </section>

      <GroundingEvidence unresolved={unresolved} proposal={proposal} />

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
