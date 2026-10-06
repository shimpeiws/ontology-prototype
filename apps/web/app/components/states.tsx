/**
 * Shared UI states from authoring-flow.md — every view uses these instead
 * of inventing per-view variants: Loading, Error, Empty.
 */

import type { ReactNode } from "react";
import { ApiError } from "../lib/api";

export function LoadingState({ label }: { label: string }) {
  return (
    <p className="state state-loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: ApiError | Error;
  onRetry?: () => void;
}) {
  const retryable = error instanceof ApiError && error.retryable;
  return (
    <div className="state state-error" role="alert">
      <p>
        <strong>
          {error instanceof ApiError ? error.code : "WEB.UNEXPECTED"}
        </strong>{" "}
        {error.message}
      </p>
      {error instanceof ApiError && error.detail !== undefined ? (
        <pre className="error-detail">{JSON.stringify(error.detail)}</pre>
      ) : null}
      {onRetry && retryable ? (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="state state-empty">
      <p>{title}</p>
      {children}
    </div>
  );
}
