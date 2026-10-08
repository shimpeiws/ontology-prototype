/**
 * Status and nature badges for the operational inspection views (#62).
 *
 * One tone mapping shared by every view so a status always means the same
 * thing visually. The acceptance rule this enforces: `not_met` /
 * `unverified` never render as success, and `unavailable` never renders
 * like `passed`. Unknown statuses fall back to a neutral badge — the
 * client renders what the API sent and never invents a verdict.
 */

export type StatusTone = "ok" | "bad" | "warn" | "unknown" | "neutral";

const TONE_BY_STATUS: Record<string, StatusTone> = {
  // Verdicts that affirm: the measured result met its bar.
  success: "ok",
  attained: "ok",
  passed: "ok",
  ok: "ok",
  accepted: "ok",
  succeeded: "ok",
  approved: "ok",
  // Verdicts that fail: measured and below the bar, or a stage that failed.
  not_met: "bad",
  missed: "bad",
  failed: "bad",
  rejected: "bad",
  // Evidence that cannot decide: no verdict either way, never success.
  unverified: "unknown",
  // Anomalous or in-flight evidence: visible, neither ok nor bad.
  // `unavailable` sits here so a guardrail with no measurement can never
  // be confused with `passed`.
  unavailable: "warn",
  pending: "warn",
  ambiguous: "warn",
  invalid: "warn",
  uncertain: "warn",
  attempting: "warn",
  proposed: "warn",
  unindexed: "warn",
  unpaired_end: "warn",
  unresolved: "warn",
};

export function statusTone(status: string | null | undefined): StatusTone {
  return (status && TONE_BY_STATUS[status]) || "neutral";
}

export function StatusBadge({
  status,
  label,
}: {
  status: string | null | undefined;
  /** Override the displayed text; defaults to the status itself. */
  label?: string;
}) {
  const text = label ?? status ?? "unknown";
  return (
    <span className={`badge status-badge status-${statusTone(status)}`}>
      {text}
    </span>
  );
}

/** Nature labels from the trace contract (docs/trace.md). */
const NATURES = new Set([
  "observed",
  "derived",
  "ai",
  "human",
  "writeback",
  "evaluated",
]);

export function NatureBadge({ nature }: { nature: string }) {
  return (
    <span
      className={`badge nature-badge nature-${NATURES.has(nature) ? nature : "unknown"}`}
    >
      {nature}
    </span>
  );
}
