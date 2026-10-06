import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  useRef,
} from "react";
import type { ReactNode } from "react";

import { api, ApiError } from "../lib/api";
import type { OpenResponse } from "../lib/types";
import type { WorkspaceFileName } from "../lib/types";
import {
  draftFilesForRequest,
  initialWorkspaceState,
  isDraftDirty,
  workspaceReducer,
} from "./store";
import type { WorkspaceState } from "./store";

export interface WorkspaceContextValue {
  state: WorkspaceState;
  /** Record a successful open response as the new active workspace. */
  open: (requestedPath: string, response: OpenResponse) => void;
  close: () => void;
  editFile: (file: WorkspaceFileName, text: string) => void;
  removeFile: (file: WorkspaceFileName) => void;
  revertFile: (file: WorkspaceFileName) => void;
  revertAll: () => void;
  /** Run draft validation through the API. No-ops while one is in flight. */
  validateDraft: () => Promise<void>;
  /**
   * Explicitly save the draft bundle through the API. No-ops while a save
   * is in flight, when the draft is clean, or when no base digest is
   * available — a save without a base can never be conflict-checked.
   */
  saveDraft: () => Promise<void>;
  /**
   * Reload the active bundle from disk (the conflict resolution path).
   * The draft is preserved — it becomes pending edits against the new
   * baseline.
   */
  reloadActive: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({
  children,
  initial,
}: {
  children: ReactNode;
  /** Test hook: seed the reducer with a pre-built state. */
  initial?: WorkspaceState;
}) {
  const [state, dispatch] = useReducer(
    workspaceReducer,
    initial ?? initialWorkspaceState,
  );
  // Monotonic request id; a stale response (superseded by a later request)
  // is discarded by the reducer instead of overwriting newer state.
  const requestSeq = useRef(0);

  const open = useCallback(
    (requestedPath: string, response: OpenResponse) =>
      dispatch({ type: "opened", requestedPath, response }),
    [],
  );
  const close = useCallback(() => dispatch({ type: "closed" }), []);
  const editFile = useCallback(
    (file: WorkspaceFileName, text: string) =>
      dispatch({ type: "edited", file, text }),
    [],
  );
  const removeFile = useCallback(
    (file: WorkspaceFileName) => dispatch({ type: "fileRemoved", file }),
    [],
  );
  const revertFile = useCallback(
    (file: WorkspaceFileName) => dispatch({ type: "reverted", file }),
    [],
  );
  const revertAll = useCallback(() => dispatch({ type: "revertedAll" }), []);

  const validateDraft = useCallback(async () => {
    if (!state.workspace || state.validating) return;
    const requestId = ++requestSeq.current;
    const revision = state.draftRevision;
    // Send the path exactly as the FDE requested it — resolution under the
    // API's configured roots is the server's job on every call.
    const path = state.workspace.requestedPath;
    const files = draftFilesForRequest(state.drafts);
    dispatch({ type: "validationStarted", requestId });
    try {
      const result = await api.validateDraft(path, files);
      dispatch({
        type: "validationFinished",
        requestId,
        revision,
        valid: result.valid,
        digest: result.digest,
        diagnostics: result.diagnostics,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      dispatch({
        type: "validationFailed",
        requestId,
        error:
          err instanceof ApiError
            ? err
            : new ApiError("WEB.UNEXPECTED", String(err), null, false),
      });
    }
  }, [state.workspace, state.validating, state.draftRevision, state.drafts]);

  const saveDraft = useCallback(async () => {
    const workspace = state.workspace;
    if (
      !workspace ||
      state.saving ||
      workspace.digest == null ||
      !isDraftDirty(state)
    ) {
      return;
    }
    // Same rule as validate: the API resolves the path under its roots on
    // every call, so send it exactly as the FDE requested it.
    const path = workspace.requestedPath;
    const baseDigest = workspace.digest;
    const files = draftFilesForRequest(state.drafts);
    const revision = state.draftRevision;
    dispatch({ type: "saveStarted" });
    try {
      const response = await api.saveWorkspace(path, baseDigest, files);
      dispatch({ type: "saveFinished", revision, files, response });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      dispatch({
        type: "saveFailed",
        error:
          err instanceof ApiError
            ? err
            : new ApiError("WEB.UNEXPECTED", String(err), null, false),
      });
    }
  }, [state]);

  const reloadActive = useCallback(async () => {
    const workspace = state.workspace;
    if (!workspace) return;
    try {
      const response = await api.openWorkspace(workspace.requestedPath);
      dispatch({ type: "reloaded", response });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      dispatch({
        type: "reloadFailed",
        error:
          err instanceof ApiError
            ? err
            : new ApiError("WEB.UNEXPECTED", String(err), null, false),
      });
    }
  }, [state.workspace]);

  const value = useMemo(
    () => ({
      state,
      open,
      close,
      editFile,
      removeFile,
      revertFile,
      revertAll,
      validateDraft,
      saveDraft,
      reloadActive,
    }),
    [
      state,
      open,
      close,
      editFile,
      removeFile,
      revertFile,
      revertAll,
      validateDraft,
      saveDraft,
      reloadActive,
    ],
  );
  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace must be used inside <WorkspaceProvider>");
  }
  return ctx;
}
