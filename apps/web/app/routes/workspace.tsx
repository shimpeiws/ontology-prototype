import { useCallback, useEffect, useRef, useState } from "react";
import {
  Navigate,
  NavLink,
  Outlet,
  useBlocker,
  useLocation,
  useNavigate,
} from "react-router";
import { Alert, Button, Layout, Menu } from "antd";

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
  { to: "/workspace/proposals", label: "Proposals" },
  { to: "/workspace/run", label: "Run" },
  { to: "/workspace/trace", label: "Trace" },
  { to: "/workspace/reports", label: "Reports" },
] as const;

/** The Menu equivalent of NavLink's `end` matching: "/workspace" lights
 *  only on the exact overview path, every other item lights on itself or
 *  any path beneath it. */
function selectedNavKey(pathname: string): string {
  for (const item of NAV_ITEMS) {
    const end = "end" in item ? item.end : false;
    const matches = end
      ? pathname === item.to
      : pathname === item.to || pathname.startsWith(`${item.to}/`);
    if (matches) return item.to;
  }
  return "/workspace";
}

export default function WorkspaceLayout() {
  const { state, close, revertAll, validateDraft, saveDraft, reloadActive } =
    useWorkspace();
  const location = useLocation();
  const navigate = useNavigate();
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
  const [armedRevertAll, setArmedRevertAll] = useState<OpenedWorkspace | null>(
    null,
  );
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
    <Layout className="workspace-layout">
      <Layout.Sider
        className="workspace-sider"
        width={184}
        breakpoint="lg"
        collapsedWidth={0}
      >
        <div className="workspace-brand">ontology</div>
        <Menu
          mode="inline"
          selectedKeys={[selectedNavKey(location.pathname)]}
          items={NAV_ITEMS.map((item) => ({
            key: item.to,
            label: (
              <NavLink to={item.to} end={"end" in item ? item.end : false}>
                {item.label}
              </NavLink>
            ),
          }))}
          // Item padding and keyboard activation land on the <li>, not the
          // anchor inside — navigate there directly; a click that already
          // hit the link is left to it so the entry is not pushed twice.
          onClick={({ key, domEvent }) => {
            if (!(domEvent.target as HTMLElement).closest("a")) {
              navigate(key);
            }
          }}
          aria-label="Workspace sections"
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="workspace-header">
          <div className="workspace-identity">
            <strong>{state.workspace.resolvedPath}</strong>
            <span className="digest">
              digest: {state.workspace.digest ?? "unavailable"}
            </span>
          </div>
          <DraftStatusBadge state={state} />
          <div className="workspace-actions">
            <Button
              onClick={() => void validateDraft()}
              disabled={state.validating}
            >
              Validate draft
            </Button>
            <Button
              type="primary"
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
            </Button>
            {confirmingRevertAll && dirty && !state.saving ? (
              <>
                <Button
                  danger
                  onClick={() => {
                    // Same guard as the other discard paths: a save already
                    // sent cannot be recalled.
                    if (state.saving) return;
                    setArmedRevertAll(null);
                    revertAll();
                  }}
                >
                  Discard all changes
                </Button>
                <Button onClick={() => setArmedRevertAll(null)}>
                  Keep draft
                </Button>
              </>
            ) : (
              <Button
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
              </Button>
            )}
            <Button
              onClick={closeWorkspace}
              disabled={state.saving}
              danger={confirmingClose}
              title={
                state.saving
                  ? "A save is in flight — wait for it to settle before closing."
                  : undefined
              }
            >
              {confirmingClose ? "Discard draft and close" : "Close workspace"}
            </Button>
          </div>
        </Layout.Header>
        <Layout.Content className="workspace-main">
          <div className="workspace-content-inner">
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
              <Alert
                role="alert"
                className="state state-error"
                type="warning"
                title="Unsaved draft changes will be discarded."
              />
            ) : null}
            {confirmingRevertAll && dirty && !state.saving ? (
              <Alert
                role="alert"
                className="state state-error"
                type="warning"
                title="Unsaved draft changes in every file will be discarded — the whole bundle returns to the active on-disk text."
              />
            ) : null}

            <Outlet />
          </div>
        </Layout.Content>
      </Layout>

      {blocker.state === "blocked" ? (
        <div
          className="blocker-dialog"
          role="alertdialog"
          aria-labelledby="blocker-title"
        >
          <p id="blocker-title">
            You have unsaved draft changes. Leave this page and discard them?
          </p>
          <Button onClick={() => blocker.reset()} autoFocus>
            Stay
          </Button>
          <Button
            danger
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
          </Button>
        </div>
      ) : null}
    </Layout>
  );
}
