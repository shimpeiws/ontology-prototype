/**
 * Workspace store: the draft/active state contract from authoring-flow.md.
 *
 * - The *active* layer is the on-disk bundle exactly as the API returned it
 *   (byte-exact file texts + digest). It is never mutated by editing.
 * - The *draft* layer holds per-file draft text. `null` means the file is
 *   absent from the draft (and is omitted from validate requests — an empty
 *   string is a real file, not a missing one).
 * - `draftRevision` increments on every draft mutation; a validation result
 *   records the revision it describes, so a draft edit renders any shown
 *   validation stale instead of silently disagreeing with it.
 * - Validation requests are sequenced: a response for a superseded request
 *   is discarded, so stale async results are never shown as current.
 */

import type {
  Diagnostic,
  OpenResponse,
  WorkspaceDefinitions,
  WorkspaceFileName,
} from "../lib/types";
import { WORKSPACE_FILE_NAMES } from "../lib/types";
import { ApiError } from "../lib/api";

export interface OpenedWorkspace {
  /** Path exactly as the FDE submitted it (may be root-relative). */
  requestedPath: string;
  /** Absolute path resolved by the API under its configured roots. */
  resolvedPath: string;
  /** Active-bundle digest; null when the API could not represent it. */
  digest: string | null;
  /** On-disk bundle validity at open time. */
  valid: boolean;
  activeFiles: Record<string, string>;
  definitions: WorkspaceDefinitions | null;
  /** Core diagnostics for the on-disk bundle, verbatim from the API. */
  diagnostics: Diagnostic[];
}

export interface DraftValidation {
  /** Draft revision this result describes. */
  revision: number;
  valid: boolean;
  digest: string;
  diagnostics: Diagnostic[];
}

export interface WorkspaceState {
  workspace: OpenedWorkspace | null;
  drafts: Partial<Record<WorkspaceFileName, string | null>>;
  draftRevision: number;
  validating: boolean;
  lastValidationRequestId: number;
  validation: DraftValidation | null;
  validationError: ApiError | null;
}

export const initialWorkspaceState: WorkspaceState = {
  workspace: null,
  drafts: {},
  draftRevision: 0,
  validating: false,
  lastValidationRequestId: 0,
  validation: null,
  validationError: null,
};

export type WorkspaceAction =
  | {
      type: "opened";
      requestedPath: string;
      response: OpenResponse;
    }
  | { type: "closed" }
  | { type: "edited"; file: WorkspaceFileName; text: string }
  | { type: "fileRemoved"; file: WorkspaceFileName }
  | { type: "reverted"; file: WorkspaceFileName }
  | { type: "revertedAll" }
  | { type: "validationStarted"; requestId: number }
  | {
      type: "validationFinished";
      requestId: number;
      revision: number;
      valid: boolean;
      digest: string;
      diagnostics: Diagnostic[];
    }
  | { type: "validationFailed"; requestId: number; error: ApiError };

export function workspaceReducer(
  state: WorkspaceState,
  action: WorkspaceAction,
): WorkspaceState {
  switch (action.type) {
    case "opened": {
      const { response } = action;
      const drafts: Partial<Record<WorkspaceFileName, string | null>> = {};
      for (const name of WORKSPACE_FILE_NAMES) {
        drafts[name] = response.files[name] ?? null;
      }
      return {
        ...initialWorkspaceState,
        workspace: {
          requestedPath: action.requestedPath,
          resolvedPath: response.workspace.path,
          digest: response.workspace.digest,
          valid: response.valid,
          activeFiles: response.files,
          definitions: response.definitions,
          diagnostics: response.diagnostics,
        },
        drafts,
      };
    }
    case "closed":
      return initialWorkspaceState;
    case "edited": {
      return {
        ...state,
        drafts: { ...state.drafts, [action.file]: action.text },
        draftRevision: state.draftRevision + 1,
      };
    }
    case "fileRemoved": {
      return {
        ...state,
        drafts: { ...state.drafts, [action.file]: null },
        draftRevision: state.draftRevision + 1,
      };
    }
    case "reverted": {
      const active = state.workspace?.activeFiles[action.file] ?? null;
      return {
        ...state,
        drafts: { ...state.drafts, [action.file]: active },
        draftRevision: state.draftRevision + 1,
      };
    }
    case "revertedAll": {
      const drafts: Partial<Record<WorkspaceFileName, string | null>> = {};
      for (const name of WORKSPACE_FILE_NAMES) {
        drafts[name] = state.workspace?.activeFiles[name] ?? null;
      }
      return {
        ...state,
        drafts,
        draftRevision: state.draftRevision + 1,
      };
    }
    case "validationStarted":
      return {
        ...state,
        validating: true,
        lastValidationRequestId: action.requestId,
        validationError: null,
      };
    case "validationFinished": {
      // A response for a superseded request is stale by definition.
      if (action.requestId !== state.lastValidationRequestId) return state;
      return {
        ...state,
        validating: false,
        validation: {
          revision: action.revision,
          valid: action.valid,
          digest: action.digest,
          diagnostics: action.diagnostics,
        },
        validationError: null,
      };
    }
    case "validationFailed": {
      if (action.requestId !== state.lastValidationRequestId) return state;
      return {
        ...state,
        validating: false,
        validationError: action.error,
      };
    }
  }
}

/** Draft files as the validate endpoint expects them: absent files omitted. */
export function draftFilesForRequest(
  drafts: Partial<Record<WorkspaceFileName, string | null>>,
): Record<string, string> {
  const files: Record<string, string> = {};
  for (const name of WORKSPACE_FILE_NAMES) {
    const text = drafts[name];
    if (text !== null && text !== undefined) files[name] = text;
  }
  return files;
}

export function isFileDirty(
  state: WorkspaceState,
  file: WorkspaceFileName,
): boolean {
  const active = state.workspace?.activeFiles[file] ?? null;
  const draft = state.drafts[file] ?? null;
  return draft !== active;
}

export function isDraftDirty(state: WorkspaceState): boolean {
  return WORKSPACE_FILE_NAMES.some((name) => isFileDirty(state, name));
}

/** True when a validation result is shown that does not match the draft. */
export function isValidationStale(state: WorkspaceState): boolean {
  return (
    state.validation !== null &&
    state.validation.revision !== state.draftRevision
  );
}
