/**
 * Frozen evaluation report view (#62): renders one `evaluation_runs`
 * record (or `evaluations/run` detail — same shape) from the operational
 * API. Cohorts, dispositions, target verdict, and guardrails are rendered
 * verbatim — including `unavailable` and `unverified`, which are honest
 * "cannot decide" evidence and must never look like `passed`/`success`.
 * The report's own no-causation note is always shown.
 */

import type {
  EvaluationCohort,
  EvaluationReport,
  EvaluationRunRecord,
} from "../lib/types";
import { EmptyState } from "./states";
import { StatusBadge } from "./StatusBadge";

function fmtValue(value: number | null | undefined, unit?: string): string {
  if (value === null || value === undefined) return "unavailable";
  // The stored value renders exactly — rounding could push a borderline
  // measurement across a strict target threshold and contradict the
  // reported verdict.
  return unit ? `${value} ${unit}` : String(value);
}

function CohortSubjects({ cohort }: { cohort: EvaluationCohort }) {
  return (
    <div className="cohort-subjects">
      {cohort.paired.length ? (
        <table className="report-table">
          <caption>paired</caption>
          <thead>
            <tr>
              <th scope="col">Subject</th>
              <th scope="col">Started</th>
              <th scope="col">Ended</th>
              <th scope="col">Duration</th>
              <th scope="col">Flags</th>
            </tr>
          </thead>
          <tbody>
            {cohort.paired.map((pair) => (
              <tr key={pair.subject_id}>
                <td>
                  <code>{pair.subject_id}</code>
                </td>
                <td>
                  <code>{pair.started_at}</code>
                </td>
                <td>
                  <code>{pair.ended_at}</code>
                </td>
                <td>{fmtValue(pair.duration)}</td>
                <td>
                  {pair.closes_after_boundary ? (
                    <StatusBadge
                      status="pending"
                      label="closes after boundary"
                    />
                  ) : null}
                  {pair.extra_end_fact_keys?.length ? (
                    <StatusBadge
                      status="ambiguous"
                      label={`+${pair.extra_end_fact_keys.length} end(s)`}
                    />
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {cohort.pending.length ? (
        <table className="report-table">
          <caption>pending — started, no end event yet</caption>
          <thead>
            <tr>
              <th scope="col">Subject</th>
              <th scope="col">Started</th>
              <th scope="col">Elapsed</th>
            </tr>
          </thead>
          <tbody>
            {cohort.pending.map((item) => (
              <tr key={item.subject_id}>
                <td>
                  <code>{item.subject_id}</code>
                </td>
                <td>
                  <code>{item.started_at}</code>
                </td>
                <td>{fmtValue(item.elapsed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {(["invalid", "ambiguous"] as const).map((kind) =>
        cohort[kind].length ? (
          <table key={kind} className="report-table">
            <caption>{kind} — excluded from aggregates</caption>
            <thead>
              <tr>
                <th scope="col">Subject</th>
                <th scope="col">Reason</th>
              </tr>
            </thead>
            <tbody>
              {cohort[kind].map((item) => (
                <tr key={item.subject_id}>
                  <td>
                    <code>{item.subject_id}</code>
                  </td>
                  <td>{item.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null,
      )}
    </div>
  );
}

function CohortBlock({
  name,
  cohort,
  unit,
  aggregation,
}: {
  name: string;
  cohort: EvaluationCohort;
  unit: string;
  aggregation: string;
}) {
  return (
    <section className="cohort" aria-label={`${name} cohort`}>
      <h3>{name}</h3>
      <dl className="definition-facts">
        <dt>
          {aggregation === "count" ? "Paired count" : `${aggregation} (${unit})`}
        </dt>
        <dd>{fmtValue(cohort.aggregate, aggregation === "count" ? undefined : unit)}</dd>
        <dt>Sample</dt>
        <dd>
          {cohort.sample}{" "}
          {cohort.sample === 1 ? "measured interval" : "measured intervals"}
        </dd>
        <dt>Subjects</dt>
        <dd>
          {(
            ["paired", "pending", "invalid", "ambiguous"] as const
          ).map((kind) => (
            <span key={kind} className="status-count">
              <StatusBadge
                status={kind}
                label={`${kind} ×${cohort.counts[kind] ?? 0}`}
              />{" "}
            </span>
          ))}
        </dd>
      </dl>
      <CohortSubjects cohort={cohort} />
    </section>
  );
}

export function EvaluationReportView({ run }: { run: EvaluationRunRecord }) {
  const report: EvaluationReport = run.report;
  const excluded = report.inputs.filter(
    (input) => input.disposition === "excluded",
  );

  return (
    <div className="evaluation-report">
      <dl className="definition-facts">
        <dt>Run</dt>
        <dd>
          <code>{run.run_id}</code>
        </dd>
        <dt>Evaluation</dt>
        <dd>{report.evaluation}</dd>
        <dt>Interval</dt>
        <dd>
          <code>{report.interval.start_event}</code> →{" "}
          <code>{report.interval.end_event}</code> on{" "}
          <code>{report.interval.subject}</code>
        </dd>
        <dt>Metric</dt>
        <dd>
          <code>
            {report.metric} ({report.aggregation}, {report.unit})
          </code>
        </dd>
        <dt>as_of</dt>
        <dd>
          <code>{report.as_of}</code>
        </dd>
        <dt>Cohort boundary</dt>
        <dd>
          <code>{report.cohort_boundary}</code>
        </dd>
        <dt>Declared baseline</dt>
        <dd>
          {fmtValue(
            report.declared_baseline,
            report.aggregation === "count" ? undefined : report.unit,
          )}
        </dd>
        <dt>Contract digest</dt>
        <dd>
          <code>{report.contract_digest}</code>
        </dd>
      </dl>

      <section aria-label="verdict">
        <h3>
          Verdict <StatusBadge status={report.overall.status} />
        </h3>
        <dl className="definition-facts">
          <dt>Target</dt>
          <dd>
            <code>
              {report.target.comparator} {report.target.value}
              {report.aggregation === "count" ? "" : ` ${report.unit}`}
            </code>{" "}
            <StatusBadge status={report.target.status} />
            {report.target.reason ? (
              <span className="hint"> — {report.target.reason}</span>
            ) : null}
          </dd>
          <dt>Change</dt>
          <dd>
            {report.change.status === "measured" ? (
              <>
                <code>
                  {fmtValue(
                    report.change.value,
                    report.aggregation === "count" ? undefined : report.unit,
                  )}
                </code>{" "}
                {/* improvement/regression are duration verdicts; the count
                    aggregation's increase/decrease is interval movement,
                    not a verdict, and stays neutral. */}
                <StatusBadge
                  status={
                    report.change.direction === "improvement"
                      ? "success"
                      : report.change.direction === "regression"
                        ? "failed"
                        : "unchanged"
                  }
                  label={report.change.direction}
                />
              </>
            ) : (
              <>
                <StatusBadge status="unavailable" label={report.change.status} />{" "}
                <span className="hint">{report.change.reason}</span>
              </>
            )}
          </dd>
        </dl>
      </section>

      <section aria-label="guardrails">
        <h3>Guardrails</h3>
        {report.guardrails.length === 0 ? (
          <EmptyState title="No guardrails in this contract." />
        ) : (
          <ul className="report-guardrails">
            {report.guardrails.map((g) => (
              <li key={g.name}>
                <strong>{g.name}</strong> — <code>{g.metric}</code>{" "}
                <code>
                  {g.comparator}
                </code>{" "}
                <StatusBadge status={g.status} />
                {g.reason ? <p className="hint">{g.reason}</p> : null}
                {g.evidence ? (
                  <details className="record-json">
                    <summary>evidence</summary>
                    <pre>{JSON.stringify(g.evidence, null, 2)}</pre>
                  </details>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="cohorts">
        <h3>Cohorts</h3>
        <div className="cohorts">
          <CohortBlock
            name="baseline"
            cohort={report.cohorts.baseline}
            unit={report.unit}
            aggregation={report.aggregation}
          />
          <CohortBlock
            name="after"
            cohort={report.cohorts.after}
            unit={report.unit}
            aggregation={report.aggregation}
          />
        </div>
      </section>

      {report.unpaired_ends.length || excluded.length ? (
        <section aria-label="unpaired and excluded subjects">
          <h3>Unpaired &amp; excluded subjects</h3>
          {report.unpaired_ends.length ? (
            <>
              <h4>Unpaired ends — an end event with no start</h4>
              <ul className="trace-list">
                {report.unpaired_ends.map((item) => (
                  <li key={item.subject_id}>
                    <code>{item.subject_id}</code>{" "}
                    <StatusBadge status="unpaired_end" label="unpaired end" />
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {excluded.length ? (
            <>
              <h4>Excluded — no visible events at as_of</h4>
              <ul className="trace-list">
                {excluded.map((input) => (
                  <li key={input.subject_id}>
                    <code>{input.subject_id}</code>{" "}
                    <span className="hint">{input.excluded}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
      ) : null}

      <p className="report-note">{report.note}</p>
    </div>
  );
}
