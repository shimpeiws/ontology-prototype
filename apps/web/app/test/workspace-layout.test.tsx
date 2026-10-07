/**
 * Workspace layout tests — synthetic fixture mode (fixtures.ts).
 * `api.validateDraft` is mocked; navigation is driven by a real memory
 * data router so useBlocker behaves as it does in production.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { transferableAbortController } from "node:util";

import { api, ApiError } from "../lib/api";
import WorkspaceLayout from "../routes/workspace";
import { WorkspaceProvider } from "../workspace/context";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import type { WorkspaceState } from "../workspace/store";
import {
  SYNTHETIC_DIAGNOSTIC,
  SYNTHETIC_OPEN_RESPONSE,
  SYNTHETIC_SAVE_RESPONSE,
  SYNTHETIC_VALIDATE_RESPONSE,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: {
      ...mod.api,
      validateDraft: vi.fn(),
      saveWorkspace: vi.fn(),
      openWorkspace: vi.fn(),
    },
  };
});

const validateDraft = vi.mocked(api.validateDraft);
const saveWorkspace = vi.mocked(api.saveWorkspace);
const openWorkspace = vi.mocked(api.openWorkspace);

// React Router creates an AbortSignal during client-side navigation and
// passes it to `new Request` — Node's undici Request, which rejects
// jsdom's AbortSignal as cross-realm. Swap in Node's implementations so
// navigations inside these tests complete.
const JSDOM_ABORT_CONTROLLER = globalThis.AbortController;
const JSDOM_ABORT_SIGNAL = globalThis.AbortSignal;
beforeAll(() => {
  const nodeController = transferableAbortController();
  globalThis.AbortController =
    nodeController.constructor as typeof AbortController;
  globalThis.AbortSignal =
    nodeController.signal.constructor as typeof AbortSignal;
});
afterAll(() => {
  globalThis.AbortController = JSDOM_ABORT_CONTROLLER;
  globalThis.AbortSignal = JSDOM_ABORT_SIGNAL;
});

function cleanState(): WorkspaceState {
  return workspaceReducer(initialWorkspaceState, {
    type: "opened",
    requestedPath: "job-invoicing",
    response: SYNTHETIC_OPEN_RESPONSE,
  });
}

function dirtyState(): WorkspaceState {
  return workspaceReducer(cleanState(), {
    type: "edited",
    file: "domain.yaml",
    text: "changed: true",
  });
}

function renderLayout(state: WorkspaceState) {
  const router = createMemoryRouter(
    [
      { path: "/", element: <p>home route</p> },
      {
        path: "/workspace",
        element: <WorkspaceLayout />,
        children: [
          { index: true, element: <p>overview page</p> },
          { path: "domain", element: <p>domain page</p> },
        ],
      },
    ],
    { initialEntries: ["/workspace"] },
  );
  render(
    <WorkspaceProvider initial={state}>
      <RouterProvider router={router} />
    </WorkspaceProvider>,
  );
  return router;
}

afterEach(() => {
  validateDraft.mockReset();
  saveWorkspace.mockReset();
  openWorkspace.mockReset();
});

describe("workspace layout", () => {
  it("blocks in-app navigation with a dirty draft; Stay dismisses", async () => {
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("link", { name: "Domain" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("unsaved draft changes");

    await user.click(screen.getByRole("button", { name: "Stay" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByText("overview page")).toBeInTheDocument();
  });

  it("Escape dismisses the leave-confirmation like Stay", async () => {
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("link", { name: "Domain" }));
    await screen.findByRole("alertdialog");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByText("overview page")).toBeInTheDocument();
  });

  it("Discard and leave proceeds to the target", async () => {
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("link", { name: "Domain" }));
    await screen.findByRole("alertdialog");
    await user.click(
      screen.getByRole("button", { name: "Discard and leave" }),
    );
    await screen.findByText("domain page");
  });

  it("Discard and leave clears the draft so later navigation is unblocked", async () => {
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("link", { name: "Domain" }));
    await screen.findByRole("alertdialog");
    await user.click(
      screen.getByRole("button", { name: "Discard and leave" }),
    );
    await screen.findByText("domain page");

    // The draft was discarded — navigating back prompts nothing.
    await user.click(screen.getByRole("link", { name: "Overview" }));
    await screen.findByText("overview page");
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("closing a dirty workspace asks for confirmation first", async () => {
    const user = userEvent.setup();
    const router = renderLayout(dirtyState());

    await user.click(
      screen.getByRole("button", { name: "Close workspace" }),
    );
    expect(
      screen.getByText("Unsaved draft changes will be discarded."),
    ).toBeInTheDocument();
    expect(screen.getByText("overview page")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Discard draft and close" }),
    );
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/"),
    );
    await screen.findByText("home route");
  });

  it("a clean workspace closes without confirmation", async () => {
    const user = userEvent.setup();
    renderLayout(cleanState());

    await user.click(
      screen.getByRole("button", { name: "Close workspace" }),
    );
    await screen.findByText("home route");
  });

  it("Revert all asks for confirmation, then clears the draft", async () => {
    const user = userEvent.setup();
    renderLayout(dirtyState());

    // The first click only arms the confirmation — nothing is discarded.
    await user.click(screen.getByRole("button", { name: "Revert all" }));
    expect(
      screen.getByText(
        /Unsaved draft changes in every file will be discarded/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("unsaved draft")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Discard all changes" }),
    );

    // The bundle is back to active: badge flips, Save disables, and
    // navigation is no longer blocked.
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.click(screen.getByRole("link", { name: "Domain" }));
    await screen.findByText("domain page");
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("cancelling the Revert all confirmation keeps the draft", async () => {
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("button", { name: "Revert all" }));
    await user.click(screen.getByRole("button", { name: "Keep draft" }));

    // The draft survives — the badge stays dirty and navigation still
    // prompts before leaving.
    expect(screen.getByText("unsaved draft")).toBeInTheDocument();
    expect(
      screen.queryByText(
        /Unsaved draft changes in every file will be discarded/,
      ),
    ).toBeNull();
    await user.click(screen.getByRole("link", { name: "Domain" }));
    await screen.findByRole("alertdialog");
  });

  it("a shown validation result goes stale after Revert all", async () => {
    let state = dirtyState();
    state = workspaceReducer(state, {
      type: "validationStarted",
      requestId: 1,
    });
    state = workspaceReducer(state, {
      type: "validationFinished",
      requestId: 1,
      revision: state.draftRevision,
      valid: false,
      digest: "d1",
      diagnostics: [SYNTHETIC_DIAGNOSTIC],
    });
    const user = userEvent.setup();
    renderLayout(state);
    expect(screen.getByText("draft invalid")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Revert all" }));
    await user.click(
      screen.getByRole("button", { name: "Discard all changes" }),
    );
    // The result keyed to the pre-revert revision can never render as
    // current again — exactly like an edit.
    expect(screen.getByText("validation stale")).toBeInTheDocument();
  });

  it("Validate draft sends the requested path and current draft files", async () => {
    validateDraft.mockResolvedValue(SYNTHETIC_VALIDATE_RESPONSE);
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(
      screen.getByRole("button", { name: "Validate draft" }),
    );
    await waitFor(() => expect(validateDraft).toHaveBeenCalledTimes(1));
    expect(validateDraft).toHaveBeenCalledWith(
      "job-invoicing",
      expect.objectContaining({ "domain.yaml": "changed: true" }),
    );
    await screen.findByText("draft invalid");
  });

  it("a validation transport failure shows message and retry", async () => {
    validateDraft.mockRejectedValue(
      new ApiError("WEB.API_UNREACHABLE", "API is down", null, true),
    );
    const user = userEvent.setup();
    renderLayout(cleanState());

    await user.click(
      screen.getByRole("button", { name: "Validate draft" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("WEB.API_UNREACHABLE");
    expect(alert).toHaveTextContent("API is down");

    validateDraft.mockResolvedValue(SYNTHETIC_VALIDATE_RESPONSE);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("draft invalid");
  });

  it("Save sends the requested path, base digest, and current draft files", async () => {
    saveWorkspace.mockResolvedValue(SYNTHETIC_SAVE_RESPONSE);
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveWorkspace).toHaveBeenCalledTimes(1));
    expect(saveWorkspace).toHaveBeenCalledWith(
      "job-invoicing",
      "synthetic-digest",
      expect.objectContaining({ "domain.yaml": "changed: true" }),
    );
    await screen.findByText(/Saved to disk/);
    // The draft is now the active bundle — the badge flips to active.
    await screen.findByText("active");
  });

  it("Save is disabled while the draft is clean", () => {
    renderLayout(cleanState());
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("Save is disabled when the active digest is unavailable", () => {
    const state = workspaceReducer(
      workspaceReducer(initialWorkspaceState, {
        type: "opened",
        requestedPath: "job-invoicing",
        response: {
          ...SYNTHETIC_OPEN_RESPONSE,
          workspace: { ...SYNTHETIC_OPEN_RESPONSE.workspace, digest: null },
        },
      }),
      { type: "edited", file: "domain.yaml", text: "changed: true" },
    );
    renderLayout(state);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("discard paths stay disabled while a save is in flight", async () => {
    // A save already sent to the API cannot be recalled, so every
    // discard path waits for it to settle — otherwise discarded edits
    // could still reach disk.
    saveWorkspace.mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("button", { name: "Revert all" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Close workspace" }),
    ).toBeDisabled();

    await user.click(screen.getByRole("link", { name: "Domain" }));
    await screen.findByRole("alertdialog");
    expect(
      screen.getByRole("button", { name: "Discard and leave" }),
    ).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Stay" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("a conflicted save offers an explicit reload that preserves the draft", async () => {
    saveWorkspace.mockResolvedValue({
      ...SYNTHETIC_SAVE_RESPONSE,
      outcome: "conflict",
      digest: "on-disk-now",
      message: "workspace files changed on disk",
    });
    const reloaded = {
      ...SYNTHETIC_OPEN_RESPONSE,
      workspace: {
        ...SYNTHETIC_OPEN_RESPONSE.workspace,
        digest: "on-disk-now",
      },
      files: {
        ...SYNTHETIC_OPEN_RESPONSE.files,
        "agent.yaml": "# changed outside the UI\n",
      },
    };
    openWorkspace.mockResolvedValue(reloaded);
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("changed on disk");

    await user.click(
      screen.getByRole("button", { name: "Reload active bundle" }),
    );
    await waitFor(() =>
      expect(openWorkspace).toHaveBeenCalledWith("job-invoicing"),
    );
    // Conflict notice cleared; the draft stays dirty against the new base.
    await waitFor(() =>
      expect(screen.queryByRole("alert")).toBeNull(),
    );
    await screen.findByText("unsaved draft");
    // Saving again sends the reloaded digest as the base.
    saveWorkspace.mockResolvedValue(SYNTHETIC_SAVE_RESPONSE);
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveWorkspace).toHaveBeenCalledTimes(2));
    expect(saveWorkspace).toHaveBeenLastCalledWith(
      "job-invoicing",
      "on-disk-now",
      expect.any(Object),
    );
  });

  it("a validation-blocked save shows a distinct state, not a transport error", async () => {
    saveWorkspace.mockResolvedValue({
      ...SYNTHETIC_SAVE_RESPONSE,
      outcome: "invalid",
      valid: false,
      diagnostics: [SYNTHETIC_DIAGNOSTIC],
    });
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("validation errors");
  });

  it("a failed save offers retry", async () => {
    saveWorkspace.mockResolvedValueOnce({
      ...SYNTHETIC_SAVE_RESPONSE,
      outcome: "failed",
      digest: null,
      message: "disk full",
    });
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Save failed");
    expect(alert).toHaveTextContent("disk full");

    saveWorkspace.mockResolvedValueOnce(SYNTHETIC_SAVE_RESPONSE);
    await user.click(screen.getByRole("button", { name: "Retry save" }));
    await screen.findByText(/Saved to disk/);
  });

  it("a save transport failure shows an error with retry, never diagnostics", async () => {
    saveWorkspace.mockRejectedValueOnce(
      new ApiError("WEB.API_UNREACHABLE", "API is down", null, true),
    );
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("WEB.API_UNREACHABLE");

    saveWorkspace.mockResolvedValueOnce(SYNTHETIC_SAVE_RESPONSE);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText(/Saved to disk/);
  });

  it("preventDefault is set on beforeunload only while drafts are unsaved", async () => {
    const { unmount } = render(
      <WorkspaceProvider initial={dirtyState()}>
        <RouterProvider
          router={createMemoryRouter(
            [
              {
                path: "/workspace",
                element: <WorkspaceLayout />,
                children: [{ index: true, element: <p>overview</p> }],
              },
            ],
            { initialEntries: ["/workspace"] },
          )}
        />
      </WorkspaceProvider>,
    );
    const dirtyEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirtyEvent);
    expect(dirtyEvent.defaultPrevented).toBe(true);
    unmount();

    render(
      <WorkspaceProvider initial={cleanState()}>
        <RouterProvider
          router={createMemoryRouter(
            [
              {
                path: "/workspace",
                element: <WorkspaceLayout />,
                children: [{ index: true, element: <p>overview</p> }],
              },
            ],
            { initialEntries: ["/workspace"] },
          )}
        />
      </WorkspaceProvider>,
    );
    const cleanEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(cleanEvent);
    expect(cleanEvent.defaultPrevented).toBe(false);
  });
});
