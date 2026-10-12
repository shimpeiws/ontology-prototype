/**
 * Proposal grounding evidence (#97): the recorded evidence dict
 * rendered as labeled, inspectable references instead of raw JSON.
 *
 * The evidence mixes references (fact keys, observation ids, ingest
 * ids) with derived context (elapsed days, event names). References
 * link into the Trace lookup (`?artifact=`); ids the recorded links
 * mark as resolving to nothing stay visible with an `unresolved`
 * badge — they are never silently dropped. When the proposal's trace
 * anchor could not be read, `links` is null: the ids render with an
 * `unverified` badge instead, since unknown is not unresolved.
 * Everything else renders as plain recorded context through the
 * generic record renderer.
 */

import { Link } from "react-router";

import type { TraceLink } from "../lib/types";
import { StatusBadge } from "./StatusBadge";
import { RecordDetails } from "./TraceView";

/** Evidence keys that hold a single artifact id. */
const REF_KEYS: { key: string; label: string }[] = [
  { key: "fact_key", label: "Fact" },
  { key: "entity_observation_id", label: "Entity observation" },
];

/** Evidence keys that hold a list of artifact ids. */
const REF_LIST_KEYS: { key: string; label: string }[] = [
  { key: "observation_ids", label: "Observations" },
  { key: "ingest_ids", label: "Ingest runs" },
];

const artifactHref = (id: string) =>
  `/workspace/trace?artifact=${encodeURIComponent(id)}`;

/** How sure the card is about one reference's target. "pending" means
 *  the anchor read is still in flight — no verdict badge yet. */
type Resolution = "resolved" | "unresolved" | "unverified" | "pending";

function RefLink({
  id,
  resolution,
}: {
  id: string;
  resolution: Resolution;
}) {
  return (
    <Link className="id-chip" to={artifactHref(id)}>
      <code>{id}</code>
      {resolution === "unresolved" ? (
        <StatusBadge status="unresolved" label="unresolved" />
      ) : resolution === "unverified" ? (
        <StatusBadge status="unverified" label="unverified" />
      ) : null}
    </Link>
  );
}

export function EvidenceReferences({
  evidence,
  links,
  fromId,
  subject,
}: {
  evidence: Record<string, unknown>;
  /** The proposal's own recorded links (from the trace anchor); the
   *  `resolved` flag marks ids pointing at no recorded artifact.
   *  `null` means the anchor could not be read — resolution is then
   *  unverified, not unresolved. `undefined` means the anchor read is
   *  still in flight — no badge is shown yet. */
  links?: TraceLink[] | null;
  /** The artifact the links were issued from (the proposal id). */
  fromId?: string;
  /** The candidate subject, shown as a subject reference. */
  subject?: { entity: string | null; id: string | null };
}) {
  const linksKnown = Array.isArray(links);
  const linksPending = links === undefined;
  const resolvedByTo = new Map<string, boolean>();
  for (const link of links ?? []) {
    if (!fromId || link.from === fromId) {
      resolvedByTo.set(link.to, link.resolved);
    }
  }
  const resolutionFor = (id: string): Resolution =>
    linksPending
      ? "pending"
      : linksKnown
        ? resolvedByTo.get(id) === true
          ? "resolved"
          : "unresolved"
        : "unverified";

  const handled = new Set<string>();
  const rows: { label: string; ids: string[] }[] = [];
  for (const { key, label } of REF_KEYS) {
    const value = evidence[key];
    if (typeof value === "string" && value) {
      rows.push({ label, ids: [value] });
      handled.add(key);
    }
  }
  for (const { key, label } of REF_LIST_KEYS) {
    const value = evidence[key];
    if (Array.isArray(value) && value.length) {
      rows.push({
        label,
        ids: value.filter((v): v is string => typeof v === "string"),
      });
      handled.add(key);
    }
  }
  // Scalar context keys stay as recorded values — never invented ids.
  const updatedAt =
    typeof evidence["entity_updated_at"] === "string"
      ? evidence["entity_updated_at"]
      : null;
  handled.add("entity_updated_at");

  const leftovers = Object.fromEntries(
    Object.entries(evidence).filter(([k]) => !handled.has(k)),
  );

  const subjectRef =
    subject?.entity && subject?.id
      ? `${subject.entity}/${subject.id}`
      : null;

  if (!subjectRef && !rows.length && !updatedAt && !Object.keys(leftovers).length) {
    return <p className="hint">No grounding evidence was recorded.</p>;
  }

  return (
    <div className="evidence-refs">
      {linksPending ? (
        <p className="hint">
          Verifying the recorded references against the trace…
        </p>
      ) : !linksKnown ? (
        <p className="hint">
          The proposal's trace anchor is not readable, so whether these
          references resolve could not be verified.
        </p>
      ) : null}
      <dl className="definition-facts">
        {subjectRef ? (
          <div className="record-field" style={{ display: "contents" }}>
            <dt>Candidate</dt>
            <dd>
              <Link
                className="id-chip"
                to={`/workspace/trace?subject=${encodeURIComponent(subjectRef)}`}
              >
                <code>{subjectRef}</code>
                {linksPending ? null : !linksKnown ? (
                  <StatusBadge status="unverified" label="unverified" />
                ) : resolvedByTo.get(subjectRef) === false ? (
                  <StatusBadge status="unresolved" label="unresolved" />
                ) : null}
              </Link>
            </dd>
          </div>
        ) : null}
        {rows.map(({ label, ids }) => (
          <div key={label} className="record-field" style={{ display: "contents" }}>
            <dt>{label}</dt>
            <dd>
              {ids.map((id) => (
                <RefLink key={id} id={id} resolution={resolutionFor(id)} />
              ))}
            </dd>
          </div>
        ))}
        {updatedAt ? (
          <div className="record-field" style={{ display: "contents" }}>
            <dt>Entity state observed at</dt>
            <dd>
              <code>{updatedAt}</code>
            </dd>
          </div>
        ) : null}
      </dl>
      {Object.keys(leftovers).length ? (
        <>
          <p className="hint">recorded context</p>
          <RecordDetails record={leftovers} />
        </>
      ) : null}
    </div>
  );
}
