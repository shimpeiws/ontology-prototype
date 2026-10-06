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
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(workspaceReducer, initialWorkspaceState);
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
    const path = state.workspace.resolvedPath;
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
