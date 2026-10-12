/**
 * Diagnosis deliverables (#95, #96): the model's recorded explanation,
 * hypotheses, and follow-up questions from a diagnosis run — rendered on
 * the Run screen (the trigger response) and on the proposal card (the
 * read-back run record).
 *
 * Everything here is model output: it carries the `ai` nature badge and
 * hypothesis wording, never an observed-fact presentation. Cited
 * evidence ids link into the Trace lookup; ids the core could not
 * verify (`unverified_evidence`) stay visible, marked unresolved.
 */

import { Link } from "react-router";

import type { DiagnosisDeliverables as Deliverables } from "../lib/types";
import { NatureBadge, StatusBadge } from "./StatusBadge";

function EvidenceChip({ id }: { id: string }) {
  return (
    <Link
      className="id-chip"
      to={`/workspace/trace?artifact=${encodeURIComponent(id)}`}
    >
      <code>{id}</code>
    </Link>
  );
}

export function DiagnosisDeliverables({
  deliverables,
}: {
  deliverables: Deliverables | null | undefined;
}) {
  const explanation =
    typeof deliverables?.explanation === "string"
      ? deliverables.explanation
      : null;
  const hypotheses = Array.isArray(deliverables?.hypotheses)
    ? deliverables.hypotheses
    : [];
  const questions = Array.isArray(deliverables?.follow_up_questions)
    ? deliverables.follow_up_questions
    : [];

  return (
    <section
      className="diagnosis-deliverables"
      aria-label="diagnosis model output"
    >
      <h4>
        Model output <NatureBadge nature="ai" />
      </h4>
      <p className="hint">
        Hypotheses the provider recorded — unconfirmed, never verified
        facts.
      </p>
      {explanation ? (
        <p className="diagnosis-explanation">{explanation}</p>
      ) : null}
      {hypotheses.length ? (
        <ul className="trace-list">
          {hypotheses.map((h, i) => {
            // The core keeps unverified ids inside `evidence` too —
            // render them only under the unverified label so a citation
            // never reads as verified in one row and unresolved in the
            // next.
            const unverified = h.unverified_evidence ?? [];
            const verifiedEvidence = (h.evidence ?? []).filter(
              (id) => !unverified.includes(id),
            );
            return (
              <li key={i}>
                <p>{h.statement ?? "…"}</p>
                {verifiedEvidence.length ? (
                  <p className="hint">
                    evidence:{" "}
                    {verifiedEvidence.map((id) => (
                      <EvidenceChip key={id} id={id} />
                    ))}
                  </p>
                ) : null}
                {unverified.length ? (
                  <p className="hint">
                    unverified evidence:{" "}
                    {unverified.map((id) => (
                      <EvidenceChip key={id} id={id} />
                    ))}
                    <StatusBadge status="unresolved" label="unresolved" />
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {questions.length ? (
        <>
          <p className="hint">follow-up questions:</p>
          <ul className="trace-list">
            {questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </>
      ) : null}
      {!explanation && !hypotheses.length && !questions.length ? (
        <p className="hint">
          No deliverables were recorded — the provider was not called or
          its output did not validate.
        </p>
      ) : null}
    </section>
  );
}
