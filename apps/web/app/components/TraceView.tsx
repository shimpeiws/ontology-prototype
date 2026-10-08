/**
 * Trace inspection views (#62): render the operational read payloads
 * (docs/api.md — `workspaces/trace*`) the way `ontology trace` presents
 * them — the linked chain observation → projection → AI output → proposal
 * → decision → receipt → later events → report, in stage order, with the
 * core's nature labels attached.
 *
 * Failed stages are evidence, not gaps: a `failed` diagnosis run, a
 * `failed`/`uncertain` execution attempt, unindexed proposals, corrupt
 * journal lines, and unresolved links are all rendered in place. These
 * components are presentational — every value shown comes verbatim from
 * the API payload.
 */

import type { ReactNode } from "react";

import type {
  SubjectTrace,
  TraceAnchor,
  TraceJournalEvent,
  TraceLink,
  TraceObservation,
  TraceOverviewResponse,
  TraceProposal,
} from "../lib/types";
import { DiagnosticsList } from "./DiagnosticsList";
import { EmptyState } from "./states";
import { NatureBadge, StatusBadge } from "./StatusBadge";

function fmtNumber(value: unknown): string {
  if (typeof value !== "number") return String(value ?? "—");
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function isFailedKind(kind: string | undefined): boolean {
  return typeof kind === "string" && kind.endsWith(".failed");
}

/** One journal line; `.failed` kinds render as failures, not hidden. */
export function JournalList({ events }: { events: TraceJournalEvent[] }) {
  if (events.length === 0) {
    return <EmptyState title="No journal events recorded." />;
  }
  return (
    <ul className="journal-list">
      {events.map((event, i) => (
        <li
          key={i}
          className={`journal-event${isFailedKind(event.kind) ? " journal-event-failed" : ""}`}
        >
          <code>{event.ts}</code> <code>{event.stage}</code>{" "}
          <code className="journal-kind">{event.kind}</code>
          {isFailedKind(event.kind) ? (
            <>
              {" "}
              <StatusBadge status="failed" />
            </>
          ) : null}
          {event.data && Object.keys(event.data).length ? (
            <pre className="journal-data">{JSON.stringify(event.data)}</pre>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function LinkList({ links }: { links: TraceLink[] }) {
  if (links.length === 0) {
    return <EmptyState title="No links recorded." />;
  }
  return (
    <ul className="link-list">
      {links.map((link, i) => (
        <li key={i} className={link.resolved ? "" : "link-unresolved"}>
          <code>{link.from}</code> → <code>{link.to}</code>{" "}
          <span className="hint">via {link.via}</span>{" "}
          {link.resolved ? null : (
            <StatusBadge status="unresolved" label="unresolved" />
          )}
        </li>
      ))}
    </ul>
  );
}

/** Generic record rendering: scalars as a facts table, the rest as JSON. */
function RecordDetails({ record }: { record: Record<string, unknown> }) {
  const scalars = Object.entries(record).filter(
    ([, v]) => v === null || typeof v !== "object",
  );
  const complex = Object.entries(record).filter(
    ([, v]) => v !== null && typeof v === "object",
  );
  return (
    <div className="record-details">
      {scalars.length ? (
        <dl className="definition-facts">
          {scalars.map(([key, value]) => (
            <div key={key} className="record-field" style={{ display: "contents" }}>
              <dt>{key}</dt>
              <dd>
                {value === null ? (
                  <em className="preview-null">null</em>
                ) : (
                  <code>{String(value)}</code>
                )}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {complex.map(([key, value]) => (
        <details key={key} className="record-json">
          <summary>
            <code>{key}</code>
          </summary>
          <pre>{JSON.stringify(value, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

function StageSection({
  title,
  nature,
  empty,
  children,
}: {
  title: string;
  nature: string;
  /** Shown when the stage left no artifacts — absence stays explicit. */
  empty: string;
  children?: ReactNode;
}) {
  return (
    <section className="trace-stage">
      <h3>
        {title} <NatureBadge nature={nature} />
      </h3>
      {children ?? <EmptyState title={empty} />}
    </section>
  );
}

/** The `ontology trace --json` overview: stage counts and run lists. */
export function TraceOverviewView({
  overview,
}: {
  overview: TraceOverviewResponse;
}) {
  const noHistory =
    (overview.journal?.events ?? 0) === 0 &&
    (overview.evidence?.observations ?? 0) === 0 &&
    (overview.projection_runs?.length ?? 0) === 0 &&
    (overview.diagnosis_runs?.length ?? 0) === 0 &&
    (overview.evaluation_runs?.length ?? 0) === 0 &&
    (overview.proposals?.total ?? 0) === 0;

  return (
    <div className="trace-overview">
      {overview.diagnostics?.length ? (
        <DiagnosticsList diagnostics={overview.diagnostics} />
      ) : null}
      {noHistory ? (
        <EmptyState title="No recorded history for this workspace yet.">
          <p>
            Run the CLI stages (ingest, project, diagnose, evaluate) to leave
            inspectable artifacts.
          </p>
        </EmptyState>
      ) : null}

      <div className="trace-sections">
        <StageSection
          title="Evidence"
          nature="observed"
          empty="No observations recorded."
        >
          {overview.evidence && overview.evidence.observations > 0 ? (
            <dl className="definition-facts">
              <dt>Ingest runs</dt>
              <dd>
                {overview.evidence.ingest_runs.map((id) => (
                  <code key={id} className="id-chip">
                    {id}
                  </code>
                ))}
              </dd>
              <dt>Observations</dt>
              <dd>{overview.evidence.observations}</dd>
              <dt>Snapshots</dt>
              <dd>{overview.evidence.snapshots}</dd>
            </dl>
          ) : undefined}
        </StageSection>

        <StageSection
          title="Canonical"
          nature="observed"
          empty="No canonical entities or facts."
        >
          {overview.canonical && overview.canonical.facts > 0 ? (
            <dl className="definition-facts">
              <dt>Entities</dt>
              <dd>{overview.canonical.entities}</dd>
              <dt>Facts</dt>
              <dd>{overview.canonical.facts}</dd>
              <dt>Fact links</dt>
              <dd>{overview.canonical.fact_links}</dd>
            </dl>
          ) : undefined}
        </StageSection>

        <StageSection
          title="Projection runs"
          nature="derived"
          empty="No projection runs."
        >
          {overview.projection_runs?.length ? (
            <ul className="run-list">
              {overview.projection_runs.map((run) => (
                <li key={run.run_id}>
                  <code>{run.run_id}</code> — <code>{run.projection}</code>{" "}
                  as of <code>{run.as_of}</code> — {run.candidates}{" "}
                  {run.candidates === 1 ? "candidate" : "candidates"}
                </li>
              ))}
            </ul>
          ) : undefined}
        </StageSection>

        <StageSection
          title="Diagnosis runs"
          nature="ai"
          empty="No diagnosis runs."
        >
          {overview.diagnosis_runs?.length ? (
            <ul className="run-list">
              {overview.diagnosis_runs.map((run) => (
                <li key={run.run_id}>
                  <code>{run.run_id}</code> — {run.provider}/{run.model}{" "}
                  <StatusBadge status={run.status} />
                </li>
              ))}
            </ul>
          ) : undefined}
        </StageSection>

        <StageSection
          title="Proposals &amp; decisions"
          nature="human"
          empty="No proposals or decisions."
        >
          {overview.proposals && overview.proposals.total > 0 ? (
            <dl className="definition-facts">
              <dt>Proposals</dt>
              <dd>
                {overview.proposals.total}
                {Object.entries(overview.proposals.by_status).map(
                  ([status, count]) => (
                    <span key={status} className="status-count">
                      {" "}
                      <StatusBadge
                        status={status}
                        label={`${status} ×${count}`}
                      />
                    </span>
                  ),
                )}
              </dd>
              {overview.proposals.unindexed > 0 ? (
                <>
                  <dt>Unindexed</dt>
                  <dd>
                    <StatusBadge
                      status="unindexed"
                      label={`${overview.proposals.unindexed} unindexed`}
                    />{" "}
                    <span className="hint">
                      recorded in a diagnosis run but not in the proposals
                      index — run `ontology proposals` to index them
                    </span>
                  </dd>
                </>
              ) : null}
              <dt>Decisions</dt>
              <dd>{overview.decisions ?? 0}</dd>
              <dt>Executions</dt>
              <dd>
                {overview.executions?.attempts ?? 0}
                {Object.entries(overview.executions?.by_status ?? {}).map(
                  ([status, count]) => (
                    <span key={status} className="status-count">
                      {" "}
                      <StatusBadge
                        status={status}
                        label={`${status} ×${count}`}
                      />
                    </span>
                  ),
                )}
              </dd>
            </dl>
          ) : undefined}
        </StageSection>

        <StageSection
          title="Evaluation runs"
          nature="evaluated"
          empty="No frozen evaluation reports."
        >
          {overview.evaluation_runs?.length ? (
            <ul className="run-list">
              {overview.evaluation_runs.map((run) => (
                <li key={run.run_id}>
                  <code>{run.run_id}</code> — {run.evaluation} as of{" "}
                  <code>{run.as_of}</code>{" "}
                  <StatusBadge status={run.overall} />
                </li>
              ))}
            </ul>
          ) : undefined}
        </StageSection>

        <StageSection
          title="Links &amp; journal"
          nature="observed"
          empty=""
        >
          <dl className="definition-facts">
            <dt>Links</dt>
            <dd>
              {overview.links?.total ?? 0}
              {(overview.links?.unresolved ?? 0) > 0 ? (
                <>
                  {" "}
                  <StatusBadge
                    status="unresolved"
                    label={`${overview.links?.unresolved} unresolved`}
                  />
                </>
              ) : null}
            </dd>
            <dt>Journal events</dt>
            <dd>{overview.journal?.events ?? 0}</dd>
            {(overview.journal?.failed_events ?? 0) > 0 ? (
              <>
                <dt>Failed events</dt>
                <dd>
                  <StatusBadge
                    status="failed"
                    label={`${overview.journal?.failed_events} failed`}
                  />
                </dd>
              </>
            ) : null}
            {(overview.journal?.unreadable_lines ?? 0) > 0 ? (
              <>
                <dt>Unreadable lines</dt>
                <dd>
                  <StatusBadge
                    status="unverified"
                    label={`${overview.journal?.unreadable_lines} unreadable`}
                  />
                </dd>
              </>
            ) : null}
          </dl>
        </StageSection>
      </div>
    </div>
  );
}

function ObservationRow({ observation }: { observation: TraceObservation }) {
  return (
    <li>
      <code>{observation.observation_id}</code> — {observation.source} row{" "}
      {observation.row} <StatusBadge status={observation.status} />{" "}
      <span className="hint">
        observed {observation.observed_at} · ingest{" "}
        <code>{observation.ingest_id}</code>
      </span>
      {observation.diagnostics.length ? (
        <DiagnosticsList diagnostics={observation.diagnostics} />
      ) : null}
    </li>
  );
}

function ProposalRow({ proposal }: { proposal: TraceProposal }) {
  return (
    <li>
      <code>{proposal.proposal_id}</code> — <code>{proposal.action}</code>{" "}
      <StatusBadge status={proposal.status} />
      {proposal.subject_id ? (
        <span className="hint">
          {" "}
          on {proposal.subject_entity}/{proposal.subject_id}
        </span>
      ) : null}
      {proposal.reason ? <p className="hint">{proposal.reason}</p> : null}
    </li>
  );
}

/** `trace --subject` payload: the whole loop for one subject. */
export function SubjectTraceView({ trace }: { trace: SubjectTrace }) {
  return (
    <div className="trace-subject">
      <h2>
        Subject <code>{`${trace.subject.entity}/${trace.subject.id}`}</code>
      </h2>

      {trace.entity ? (
        <section className="trace-stage">
          <h3>
            Current entity state <NatureBadge nature="observed" />
          </h3>
          <RecordDetails record={trace.entity} />
        </section>
      ) : null}

      <StageSection
        title="Observations"
        nature="observed"
        empty="No observation evidence touches this subject."
      >
        {trace.observations.length ? (
          <ul className="trace-list">
            {trace.observations.map((o) => (
              <ObservationRow key={o.observation_id} observation={o} />
            ))}
          </ul>
        ) : undefined}
      </StageSection>

      <StageSection
        title="Facts"
        nature="observed"
        empty="No canonical facts for this subject."
      >
        {trace.facts.length ? (
          <ul className="trace-list">
            {trace.facts.map((fact) => (
              <li key={fact.fact_key}>
                <code>{fact.event}</code> <code>{fact.fact_key}</code>{" "}
                <span className="hint">
                  occurred {fact.occurred_at} · observed {fact.observed_at}
                </span>
                {fact.links.length ? (
                  <ul>
                    {fact.links.map((link, i) => (
                      <li key={i}>
                        <span className="hint">
                          {link.relation} → <code>{link.target}</code>
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        ) : undefined}
      </StageSection>

      <StageSection
        title="Projection runs"
        nature="derived"
        empty="No projection run touched this subject."
      >
        {trace.projections.length ? (
          <ul className="trace-list">
            {trace.projections.map((run) => (
              <li key={run.run_id}>
                <code>{run.run_id}</code> — <code>{run.projection}</code>{" "}
                <StatusBadge status="neutral" label={run.role} />{" "}
                <span className="hint">as of {run.as_of}</span>
              </li>
            ))}
          </ul>
        ) : undefined}
      </StageSection>

      <StageSection
        title="Diagnosis runs"
        nature="ai"
        empty="No diagnosis run covered this subject."
      >
        {trace.diagnoses.length ? (
          <ul className="trace-list">
            {trace.diagnoses.map((run) => (
              <li key={run.run_id}>
                <code>{run.run_id}</code> — {run.provider}/{run.model}{" "}
                <StatusBadge status={run.status} />
                {run.provider_called === false ? (
                  <span className="hint"> provider not called</span>
                ) : null}
                {run.proposals.length ? (
                  <ul>
                    {run.proposals.map((id) => (
                      <li key={id}>
                        <code>{id}</code>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        ) : undefined}
      </StageSection>

      <StageSection
        title="Proposals"
        nature="ai"
        empty="No proposals for this subject."
      >
        {trace.proposals.length ? (
          <ul className="trace-list">
            {trace.proposals.map((p) => (
              <ProposalRow key={p.proposal_id} proposal={p} />
            ))}
          </ul>
        ) : undefined}
      </StageSection>

      <StageSection
        title="Decisions"
        nature="human"
        empty="No human decisions recorded."
      >
        {trace.decisions.length ? (
          <ul className="trace-list">
            {trace.decisions.map((d) => (
              <li key={d.decision_id}>
                <code>{d.decision_id}</code>{" "}
                <StatusBadge status={d.decision} /> by {d.reviewer} on{" "}
                <code>{d.proposal_id}</code>{" "}
                <span className="hint">{d.decided_at}</span>
                {d.reason ? <p className="hint">{d.reason}</p> : null}
              </li>
            ))}
          </ul>
        ) : undefined}
      </StageSection>

      <StageSection
        title="Executions"
        nature="writeback"
        empty="No write-back attempts recorded."
      >
        {trace.executions.length ? (
          <ul className="trace-list">
            {trace.executions.map((attempt) => (
              <li key={attempt.execution_id}>
                <code>{attempt.execution_id}</code> —{" "}
                <code>{attempt.action}</code> via {attempt.adapter} attempt{" "}
                {attempt.attempt} <StatusBadge status={attempt.status} />
                {attempt.error ? (
                  <p className="hint">error: {attempt.error}</p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : undefined}
      </StageSection>

      <StageSection
        title="Later facts correlated with the intervention"
        nature="observed"
        empty="No correlated facts recorded."
      >
        {trace.correlated_facts.length ? (
          <ul className="trace-list">
            {trace.correlated_facts.map((key) => (
              <li key={key}>
                <code>{key}</code>
              </li>
            ))}
          </ul>
        ) : undefined}
      </StageSection>

      <StageSection
        title="Evaluations"
        nature="evaluated"
        empty="This subject appears in no frozen evaluation report."
      >
        {trace.evaluations.length ? (
          <ul className="trace-list">
            {trace.evaluations.map((run) => (
              <li key={run.run_id}>
                <code>{run.run_id}</code> — {run.evaluation}{" "}
                <StatusBadge status={run.overall} />{" "}
                <span className="hint">
                  disposition {run.subject.disposition ?? "—"}
                  {run.subject.cohort ? ` · ${run.subject.cohort}` : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : undefined}
      </StageSection>

      <StageSection
        title="Journal"
        nature="observed"
        empty=""
      >
        <JournalList events={trace.journal} />
      </StageSection>

      <StageSection title="Links" nature="observed" empty="">
        <LinkList links={trace.links} />
      </StageSection>
    </div>
  );
}

/** `trace --run` payload: one artifact, its links, and journal mentions. */
export function AnchorTraceView({
  trace,
  onFollowSubject,
}: {
  trace: TraceAnchor;
  onFollowSubject?: (subject: string) => void;
}) {
  return (
    <div className="trace-anchor">
      <h2>
        Artifact <code>{trace.id}</code>
      </h2>
      <p>
        <code>{trace.kind}</code> <NatureBadge nature={trace.nature} />
      </p>

      {trace.record ? (
        <section className="trace-stage">
          <h3>Record</h3>
          <RecordDetails record={trace.record} />
        </section>
      ) : null}

      {trace.subjects?.length ? (
        <section className="trace-stage">
          <h3>Subjects</h3>
          <ul className="trace-list">
            {trace.subjects.map((subject) => (
              <li key={subject}>
                {onFollowSubject ? (
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => onFollowSubject(subject)}
                  >
                    {subject}
                  </button>
                ) : (
                  <code>{subject}</code>
                )}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <StageSection title="Links" nature="observed" empty="">
        <LinkList links={trace.links} />
      </StageSection>

      <StageSection title="Journal" nature="observed" empty="">
        <JournalList events={trace.journal} />
      </StageSection>
    </div>
  );
}
