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
  SaveOutcome,
  SaveResponse,
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

/**
 * The outcome of the last save attempt (authoring-flow.md save contract:
 * success, validation-blocked, conflict, and retryable failure are
 * distinct states). Stays until the next save attempt or a reload — it
 * records what the last save did, while the draft badge independently
 * shows whether edits happened since.
 */
export interface SaveNotice {
  outcome: SaveOutcome;
  /**
   * New active digest on `saved`; the current on-disk digest on
   * `conflict` — the base a reloaded draft saves against; null elsewhere.
   */
  digest: string | null;
  message: string | null;
}

export interface WorkspaceState {
  workspace: OpenedWorkspace | null;
  drafts: Partial<Record<WorkspaceFileName, string | null>>;
  draftRevision: number;
  validating: boolean;
  lastValidationRequestId: number;
  validation: DraftValidation | null;
  validationError: ApiError | null;
  saving: boolean;
  saveNotice: SaveNotice | null;
  /** Transport-level save/reload failure — never a validation finding. */
  saveError: ApiError | null;
}

export const initialWorkspaceState: WorkspaceState = {
  workspace: null,
  drafts: {},
  draftRevision: 0,
  validating: false,
  lastValidationRequestId: 0,
  validation: null,
  validationError: null,
  saving: false,
  saveNotice: null,
  saveError: null,
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
  | { type: "validationFailed"; requestId: number; error: ApiError }
  | { type: "saveStarted" }
  | {
      type: "saveFinished";
      /** Draft revision at the moment the save was submitted. */
      revision: number;
      /** The exact files that were written on a `saved` outcome. */
      files: Record<string, string>;
      response: SaveResponse;
    }
  | { type: "saveFailed"; error: ApiError }
  | {
      type: "reloaded";
      response: OpenResponse;
    }
  | { type: "reloadFailed"; error: ApiError };

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
    case "saveStarted":
      return {
        ...state,
        saving: true,
        saveNotice: null,
        saveError: null,
      };
    case "saveFinished": {
      const { response } = action;
      const next: WorkspaceState = {
        ...state,
        saving: false,
        saveError: null,
        saveNotice: {
          outcome: response.outcome,
          digest: response.digest,
          message: response.message,
        },
      };
      if (response.outcome === "saved" && state.workspace) {
        // The files sent are the new active bundle. Drafts are untouched:
        // if the FDE kept editing while the save was in flight, the draft
        // stays dirty against the new baseline; otherwise it is clean.
        next.workspace = {
          ...state.workspace,
          digest: response.digest,
          valid: response.valid,
          activeFiles: { ...action.files },
          definitions: response.definitions,
          diagnostics: response.diagnostics,
        };
        // The save's diagnostics describe exactly the saved content —
        // they are the freshest validation result for that revision.
        next.validation = {
          revision: action.revision,
          valid: response.valid,
          digest: response.digest ?? "",
          diagnostics: response.diagnostics,
        };
      } else if (response.outcome === "invalid") {
        // The save ran whole-bundle validation server-side; surface those
        // diagnostics everywhere validation output is shown.
        next.validation = {
          revision: action.revision,
          valid: false,
          digest: response.digest ?? "",
          diagnostics: response.diagnostics,
        };
      }
      // `conflict` and `failed` change no workspace state: the draft is
      // preserved for the review/retry path.
      return next;
    }
    case "saveFailed":
      return {
        ...state,
        saving: false,
        saveError: action.error,
      };
    case "reloaded": {
      // The conflict path's "reload": the active baseline updates to what
      // is on disk now, but the draft is preserved — it is the thing the
      // FDE is reviewing against the new baseline. Dirty state is
      // recomputed against the new active files; a draft file identical
      // to the reloaded content simply stops being dirty.
      if (!state.workspace) return state;
      return {
        ...state,
        workspace: {
          requestedPath: state.workspace.requestedPath,
          resolvedPath: action.response.workspace.path,
          digest: action.response.workspace.digest,
          valid: action.response.valid,
          activeFiles: action.response.files,
          definitions: action.response.definitions,
          diagnostics: action.response.diagnostics,
        },
        saveNotice: null,
        saveError: null,
      };
    }
    case "reloadFailed":
      return {
        ...state,
        saveError: action.error,
      };
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
