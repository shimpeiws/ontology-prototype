/**
 * Shared UI states from authoring-flow.md — every view uses these instead
 * of inventing per-view variants: Loading, Error, Empty.
 */

import type { ReactNode } from "react";
import { Alert, Button, Empty, Spin } from "antd";

import { ApiError } from "../lib/api";

export function LoadingState({ label }: { label: string }) {
  return (
    <div className="state state-loading" role="status" aria-live="polite">
      <Spin size="small" aria-hidden="true" />
      {label}
    </div>
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
    <Alert
      className="state state-error"
      type="error"
      role="alert"
      title={
        <>
          <strong>
            {error instanceof ApiError ? error.code : "WEB.UNEXPECTED"}
          </strong>{" "}
          {error.message}
        </>
      }
      description={
        error instanceof ApiError && error.detail !== undefined ? (
          <pre className="error-detail">{JSON.stringify(error.detail)}</pre>
        ) : undefined
      }
      action={
        onRetry && retryable ? (
          <Button size="small" onClick={onRetry}>
            Retry
          </Button>
        ) : undefined
      }
    />
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
    <Empty
      className="state state-empty"
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={title}
    >
      {children}
    </Empty>
  );
}
