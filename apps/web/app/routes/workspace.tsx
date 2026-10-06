import { useEffect, useState } from "react";
import {
  Navigate,
  NavLink,
  Outlet,
  useBlocker,
  useNavigate,
} from "react-router";

import { useWorkspace } from "../workspace/context";
import { isDraftDirty } from "../workspace/store";
import { DraftStatusBadge } from "../components/DraftStatus";

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
  const { state, close, validateDraft } = useWorkspace();
  const navigate = useNavigate();
  const dirty = isDraftDirty(state);
  const blocker = useBlocker(dirty);
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
    close();
    navigate("/");
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
          <button type="button" onClick={closeWorkspace}>
            {confirmingClose ? "Discard draft and close" : "Close workspace"}
          </button>
        </div>
      </header>
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
          aria-modal="true"
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
