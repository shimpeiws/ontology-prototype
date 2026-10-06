import type { ReactNode } from "react";

import { useWorkspace } from "../workspace/context";
import { isValidationStale } from "../workspace/store";
import { DiagnosticsList } from "./DiagnosticsList";
import { DraftEditor } from "./DraftEditor";
import type { WorkspaceFileName } from "../lib/types";

/**
 * Shared layout for one definition's page: the typed view (or a note when
 * the bundle has errors and no parsed definition exists), this file's
 * diagnostics, and the draft editor.
 */
export function DefinitionPage({
  title,
  file,
  children,
}: {
  title: string;
  file: WorkspaceFileName;
  /** Rendered only when parsed definitions are available. */
  children?: ReactNode;
}) {
  const { state } = useWorkspace();
  const hasDefinitions = state.workspace?.definitions != null;

  return (
    <div className="definition-page">
      <h1>{title}</h1>
      {hasDefinitions ? (
        children
      ) : (
        <p className="hint">
          No parsed definition — the active bundle has errors. Fix the draft
          and validate; diagnostics for <code>{file}</code> are listed below.
        </p>
      )}
      {state.validation && isValidationStale(state) ? (
        <p className="hint" role="status">
          The draft changed after validation ran — diagnostics below are
          stale. Run “Validate draft” again.
        </p>
      ) : null}
      <DiagnosticsList
        diagnostics={
          state.validation
            ? state.validation.diagnostics
            : (state.workspace?.diagnostics ?? [])
        }
        file={file}
      />
      <DraftEditor file={file} />
    </div>
  );
}
