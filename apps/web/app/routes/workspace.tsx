import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, NavLink, Outlet, useBlocker } from "react-router";

import { useWorkspace } from "../workspace/context";
import { isDraftDirty } from "../workspace/store";
import type { OpenedWorkspace } from "../workspace/store";
import { DraftStatusBadge } from "../components/DraftStatus";
import { SaveNoticeView } from "../components/SaveNotice";
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
  const {
    state,
    close,
    revertAll,
    validateDraft,
    saveDraft,
    reloadActive,
  } = useWorkspace();
  const dirty = isDraftDirty(state);
  // The blocker must consult the latest render's state, not the closure it
  // was registered with: React runs a descendant <Navigate>'s effect before
  // this component's blocker re-registration, so a stale dirty=true would
  // block the redirect that happens right after Close on a dirty draft.
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const blocker = useBlocker(useCallback(() => dirtyRef.current, []));
  const [confirmingClose, setConfirmingClose] = useState(false);
  // "Revert all" discards every draft file, so it arms an explicit
  // confirmation first — never a one-click discard. The arm is bound to
  // the active baseline it described: the reducer replaces the workspace
  // object whenever that baseline moves (a landed save or a reload), so
  // an arm taken against the old baseline can never fire a revert-all
  // against different on-disk text. An arm is moot once the draft is
  // clean, and a save starting disarms it rather than letting it
  // reappear after — the baseline may move when the save lands.
  const [armedRevertAll, setArmedRevertAll] =
    useState<OpenedWorkspace | null>(null);
  const confirmingRevertAll =
    armedRevertAll !== null && armedRevertAll === state.workspace;
  useEffect(() => {
    if (!confirmingRevertAll || !dirty || state.saving) {
      setArmedRevertAll(null);
    }
    if (!dirty || state.saving) {
      setConfirmingClose(false);
    }
  }, [confirmingRevertAll, dirty, state.saving]);

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
    // A save already sent to the API cannot be recalled — discarding the
    // draft while it is in flight would let the discarded edits still
    // reach disk. Wait for the save to settle instead.
    if (state.saving) return;
    if (dirty && !confirmingClose) {
      setConfirmingClose(true);
      setArmedRevertAll(null);
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
          <button
            type="button"
            onClick={() => void saveDraft()}
            disabled={
              state.saving ||
              state.reloading ||
              !dirty ||
              state.workspace.digest === null
            }
            title={
              state.workspace.digest === null
                ? "The active bundle digest is unavailable, so a save can never be conflict-checked — reload the workspace."
                : undefined
            }
          >
            {state.saving ? "Saving…" : "Save"}
          </button>
          {confirmingRevertAll && dirty && !state.saving ? (
            <>
              <button
                type="button"
                onClick={() => {
                  // Same guard as the other discard paths: a save already
                  // sent cannot be recalled.
                  if (state.saving) return;
                  setArmedRevertAll(null);
                  revertAll();
                }}
              >
                Discard all changes
              </button>
              <button
                type="button"
                onClick={() => setArmedRevertAll(null)}
              >
                Keep draft
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => {
                setConfirmingClose(false);
                setArmedRevertAll(state.workspace);
              }}
              disabled={!dirty || state.saving}
              title={
                state.saving
                  ? "A save is in flight — wait for it to settle before discarding the draft."
                  : undefined
              }
            >
              Revert all
            </button>
          )}
          <button
            type="button"
            onClick={closeWorkspace}
            disabled={state.saving}
            title={
              state.saving
                ? "A save is in flight — wait for it to settle before closing."
                : undefined
            }
          >
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
      {state.saveError ? (
        <ErrorState
          error={state.saveError}
          onRetry={() => void saveDraft()}
        />
      ) : null}
      {state.saveNotice ? (
        <SaveNoticeView
          notice={state.saveNotice}
          onReload={() => void reloadActive()}
          onRetrySave={() => void saveDraft()}
        />
      ) : null}
      {confirmingClose ? (
        <p role="alert" className="state state-error">
          Unsaved draft changes will be discarded.
        </p>
      ) : null}
      {confirmingRevertAll && dirty && !state.saving ? (
        <p role="alert" className="state state-error">
          Unsaved draft changes in every file will be discarded — the whole
          bundle returns to the active on-disk text.
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
          <button
            type="button"
            disabled={state.saving}
            title={
              state.saving
                ? "A save is in flight — discard only after it settles, or the discarded edits could still reach disk."
                : undefined
            }
            onClick={() => {
              if (state.saving) return;
              // The draft outlives this navigation (the provider sits
              // above the layout), so "discard" must clear it explicitly
              // or every later navigation prompts again.
              revertAll();
              blocker.proceed();
            }}
          >
            Discard and leave
          </button>
        </div>
      ) : null}
    </div>
  );
}
