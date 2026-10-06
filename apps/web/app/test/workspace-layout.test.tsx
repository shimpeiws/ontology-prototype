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
  SYNTHETIC_OPEN_RESPONSE,
  SYNTHETIC_VALIDATE_RESPONSE,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: { ...mod.api, validateDraft: vi.fn() },
  };
});

const validateDraft = vi.mocked(api.validateDraft);

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

  it("Revert all clears the draft so navigation is no longer blocked", async () => {
    const user = userEvent.setup();
    renderLayout(dirtyState());

    await user.click(screen.getByRole("button", { name: "Revert all" }));
    await user.click(screen.getByRole("link", { name: "Domain" }));
    await screen.findByText("domain page");
    expect(screen.queryByRole("alertdialog")).toBeNull();
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
