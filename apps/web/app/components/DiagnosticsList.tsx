/**
 * Core diagnostics, rendered verbatim from the API contract:
 * severity, code, message, file, dotted path, line, hint.
 * The diagnostics channel carries core validation findings only —
 * transport errors are rendered by ErrorState instead.
 */

import type { Diagnostic } from "../lib/types";
import { EmptyState } from "./states";

export function DiagnosticsList({
  diagnostics,
  file,
}: {
  diagnostics: Diagnostic[];
  /** When set, only diagnostics for this workspace file are shown. */
  file?: string;
}) {
  const shown = file
    ? diagnostics.filter((d) => d.file === file)
    : diagnostics;
  if (shown.length === 0) {
    return <EmptyState title="No diagnostics." />;
  }
  return (
    <ul className="diagnostics-list">
      {shown.map((d, i) => (
        <li key={i} className={`diagnostic severity-${d.severity}`}>
          <span className="diagnostic-severity">{d.severity}</span>{" "}
          <code className="diagnostic-code">{d.code}</code>{" "}
          <span className="diagnostic-message">{d.message}</span>
          <span className="diagnostic-location">
            {[d.file, d.path, d.line !== null ? `line ${d.line}` : null]
              .filter(Boolean)
              .join(" — ")}
          </span>
          {d.hint ? <span className="diagnostic-hint">{d.hint}</span> : null}
        </li>
      ))}
    </ul>
  );
}
