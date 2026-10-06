import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, NavLink, Outlet, useBlocker } from "react-router";

import { useWorkspace } from "../workspace/context";
import { isDraftDirty } from "../workspace/store";
import { DraftStatusBadge } from "../components/DraftStatus";
import { ErrorState } from "../components/states";

export function meta() {
  return [{ title: "ontology — workspace" }];
}

const NAV_ITEMS = [
  { to: "/workspace", label: "Overview", end: true },
  { to: "/workspace/domain", label: "Domain" },
  { to: "/workspace/mapping", label: "Mapping" },
  { to: "/workspace/evaluation", label: "Evaluation" },
  { to: "/workspace/agent", label: "Agent task" },
] as const;

export default function WorkspaceLayout() {
  const { state, close, revertAll, validateDraft } = useWorkspace();
  const dirty = isDraftDirty(state);
  // The blocker must consult the latest render's state, not the closure it
  // was registered with: React runs a descendant <Navigate>'s effect before
  // this component's blocker re-registration, so a stale dirty=true would
  // block the redirect that happens right after Close on a dirty draft.
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const blocker = useBlocker(useCallback(() => dirtyRef.current, []));
  const [confirmingClose, setConfirmingClose] = useState(false);

  // Warn on tab close / reload with unsaved drafts — useBlocker only covers
  // in-app navigation.
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  // Escape dismisses the leave-confirmation like "Stay". This is a plain
  // alertdialog, not a modal — no focus trap or inert background is claimed.
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") blocker.reset();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [blocker]);

  if (!state.workspace) {
    // In-memory state is gone (reload or direct visit) — back to open form.
    return <Navigate to="/" replace />;
  }

  function closeWorkspace() {
    if (dirty && !confirmingClose) {
      setConfirmingClose(true);
      return;
    }
    setConfirmingClose(false);
    // After close, workspace is null and the layout redirects to "/".
    close();
  }

  return (
    <div className="workspace-layout">
      <header className="workspace-header">
        <div className="workspace-identity">
          <strong>{state.workspace.resolvedPath}</strong>
          <span className="digest">
            digest: {state.workspace.digest ?? "unavailable"}
          </span>
        </div>
        <DraftStatusBadge state={state} />
        <div className="workspace-actions">
          <button
            type="button"
            onClick={() => void validateDraft()}
            disabled={state.validating}
          >
            Validate draft
          </button>
          <button type="button" onClick={revertAll} disabled={!dirty}>
            Revert all
          </button>
          <button type="button" onClick={closeWorkspace}>
            {confirmingClose ? "Discard draft and close" : "Close workspace"}
          </button>
        </div>
      </header>
      {state.validationError ? (
        <ErrorState
          error={state.validationError}
          onRetry={() => void validateDraft()}
        />
      ) : null}
      {confirmingClose ? (
        <p role="alert" className="state state-error">
          Unsaved draft changes will be discarded.
        </p>
      ) : null}

      <nav className="workspace-nav" aria-label="Workspace definitions">
        <ul>
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={"end" in item ? item.end : false}
              >
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <main className="workspace-main">
        <Outlet />
      </main>

      {blocker.state === "blocked" ? (
        <div
          className="blocker-dialog"
          role="alertdialog"
          aria-labelledby="blocker-title"
        >
          <p id="blocker-title">
            You have unsaved draft changes. Leave this page and discard them?
          </p>
          <button type="button" onClick={() => blocker.reset()} autoFocus>
            Stay
          </button>
          <button type="button" onClick={() => blocker.proceed()}>
            Discard and leave
          </button>
        </div>
      ) : null}
    </div>
  );
}
