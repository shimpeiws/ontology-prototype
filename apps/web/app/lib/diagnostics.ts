/**
 * Shared diagnostic selection: which diagnostics a page shows and how a
 * core `path` maps onto a rendered field.
 *
 * The diagnostics channel carries core validation findings only — these
 * helpers filter and address them, never reinterpret them (INV-004).
 */

import type { Diagnostic } from "./types";
import type { WorkspaceState } from "../workspace/store";

/**
 * The diagnostic set a page should render: the latest draft validation
 * when one exists (even a stale one — callers surface staleness), else the
 * active-bundle diagnostics from workspace open.
 */
export function currentDiagnostics(state: WorkspaceState): Diagnostic[] {
  return state.validation
    ? state.validation.diagnostics
    : (state.workspace?.diagnostics ?? []);
}

/**
 * Diagnostics for one workspace file plus bundle-level ones (`file: null`,
 * e.g. WORKSPACE.MISSING_FILE) — the latter must never be filtered out.
 */
export function diagnosticsForFile(
  state: WorkspaceState,
  file: string,
): Diagnostic[] {
  return currentDiagnostics(state).filter(
    (d) => d.file === file || d.file === null,
  );
}

/**
 * Diagnostics whose `path` is exactly `prefix` or underneath it —
 * e.g. prefix `domain.entities.Job` matches `domain.entities.Job` and
 * `domain.entities.Job.properties.status.type`.
 */
export function diagnosticsAtPath(
  diagnostics: Diagnostic[],
  prefix: string,
): Diagnostic[] {
  return diagnostics.filter(
    (d) =>
      d.path !== null &&
      (d.path === prefix || d.path.startsWith(`${prefix}.`)),
  );
}
