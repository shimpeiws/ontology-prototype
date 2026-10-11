/**
 * Armed write-back trigger (#84 / ADR 0004): executes one approved
 * proposal through the API.
 *
 * Two-step — "Execute proposal" arms, then "Confirm write-back" fires
 * the POST. The approval gate itself lives in the shared core, so a
 * refusal arrives as diagnostics (the same codes `ontology execute`
 * prints), never as a silently swallowed click. Every outcome renders
 * in place: the receipt on success — with the replayed "already
 * executed" state marked — the refusal diagnostics on a domain
 * failure, and transport errors through the shared error surface.
 */

import { useRef, useState } from "react";
import { Alert, Button } from "antd";

import { api, ApiError } from "../lib/api";
import type { Diagnostic, ExecuteResponse } from "../lib/types";
import { DiagnosticsList } from "./DiagnosticsList";
import { ErrorState } from "./states";
import { StatusBadge } from "./StatusBadge";

function toError(err: unknown): ApiError | Error {
  return err instanceof Error ? err : new Error(String(err));
}

type ExecuteOutcome =
  | { kind: "done"; response: ExecuteResponse }
  | { kind: "domain"; diagnostics: Diagnostic[] }
  | { kind: "transport"; error: ApiError | Error };

export function ExecuteControl({
  path,
  proposalId,
  disabled,
  disabledReason,
  onExecuted,
}: {
  path: string;
  proposalId: string;
  /** When true, the arm button is inert — e.g. no approved decision. */
  disabled?: boolean;
  /** Why the control is disabled, rendered as a hint. */
  disabledReason?: string;
  /** Called after a successful execution so lists can refresh. */
  onExecuted?: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<ExecuteOutcome | null>(null);
  // Single-flight: a superseded attempt's late response is discarded.
  const seq = useRef(0);

  const run = async () => {
    const attempt = ++seq.current;
    setPending(true);
    setOutcome(null);
    try {
      const response = await api.execute(path, proposalId);
      if (attempt !== seq.current) return;
      if (response.ok) {
        setOutcome({ kind: "done", response });
        setArmed(false);
      } else {
        setOutcome({
          kind: "domain",
          diagnostics: response.diagnostics ?? [],
        });
      }
      // A reached-server response can record an attempt even when it
      // was refused — refresh the execution history either way.
      onExecuted?.();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      if (attempt !== seq.current) return;
      setOutcome({ kind: "transport", error: toError(err) });
    } finally {
      if (attempt === seq.current) setPending(false);
    }
  };

  return (
    <div className="execute-control">
      {armed ? (
        <div className="execute-confirm">
          <Alert
            className="state state-error"
            type="warning"
            role="alert"
            title={
              <>
                This runs the write-back for <code>{proposalId}</code> —
                the same core gate as <code>ontology execute</code>.
              </>
            }
          />
          <div className="add-row">
            <Button
              danger
              disabled={pending}
              onClick={() => void run()}
            >
              {pending ? "Executing…" : "Confirm write-back"}
            </Button>
            <Button
              disabled={pending}
              onClick={() => setArmed(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="add-row">
          <Button
            disabled={disabled || pending}
            title={disabled ? disabledReason : undefined}
            onClick={() => {
              setOutcome(null);
              setArmed(true);
            }}
          >
            Execute proposal
          </Button>
          {disabled && disabledReason ? (
            <p className="hint">{disabledReason}</p>
          ) : null}
        </div>
      )}

      {outcome?.kind === "done" ? (
        <div className="execute-receipt" role="status">
          <p>
            <StatusBadge status={outcome.response.status ?? "unknown"} />{" "}
            execution <code>{outcome.response.execution_id}</code>{" "}
            attempt {outcome.response.attempt}
            {outcome.response.replayed ? (
              <span className="hint">
                {" "}
                — already executed; no new write was appended
              </span>
            ) : null}
          </p>
          {outcome.response.receipt ? (
            <details className="record-json">
              <summary>receipt</summary>
              <pre>
                {JSON.stringify(outcome.response.receipt, null, 2)}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}
      {outcome?.kind === "domain" ? (
        <DiagnosticsList diagnostics={outcome.diagnostics} />
      ) : null}
      {outcome?.kind === "transport" ? (
        <ErrorState
          error={outcome.error}
          onRetry={
            outcome.error instanceof ApiError && outcome.error.retryable
              ? () => void run()
              : undefined
          }
        />
      ) : null}
    </div>
  );
}
