/**
 * Open-form flow tests — synthetic fixture mode: `api.openWorkspace` is
 * mocked; no real API is contacted (see fixtures.ts).
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "../lib/api";
import { useWorkspace, WorkspaceProvider } from "../workspace/context";
import Home from "../routes/home";
import { SYNTHETIC_OPEN_RESPONSE } from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: { ...mod.api, openWorkspace: vi.fn() },
  };
});

const openWorkspace = vi.mocked(api.openWorkspace);

function Probe() {
  const { state } = useWorkspace();
  return <output data-testid="opened">{state.workspace?.resolvedPath}</output>;
}

function renderHome() {
  return render(
    <MemoryRouter>
      <WorkspaceProvider>
        <Home />
        <Probe />
      </WorkspaceProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  openWorkspace.mockReset();
});

describe("workspace open form", () => {
  it("path input is labeled and submit is a real button", () => {
    renderHome();
    expect(
      screen.getByLabelText("Workspace path"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open workspace" }),
    ).toBeInTheDocument();
  });

  it("empty submission shows a field error and does not call the API", async () => {
    const user = userEvent.setup();
    renderHome();
    await user.clear(screen.getByLabelText("Workspace path"));
    await user.click(
      screen.getByRole("button", { name: "Open workspace" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter a workspace path.",
    );
    expect(openWorkspace).not.toHaveBeenCalled();
  });

  it("successful open records the workspace", async () => {
    openWorkspace.mockResolvedValue(SYNTHETIC_OPEN_RESPONSE);
    const user = userEvent.setup();
    renderHome();
    await user.click(
      screen.getByRole("button", { name: "Open workspace" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("opened")).toHaveTextContent(
        "/abs/example/job-invoicing",
      ),
    );
    expect(openWorkspace).toHaveBeenCalledWith("job-invoicing");
  });

  it("API failure shows a structured error, retryable when transient", async () => {
    openWorkspace.mockRejectedValue(
      new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    );
    const user = userEvent.setup();
    renderHome();
    await user.click(
      screen.getByRole("button", { name: "Open workspace" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("WEB.API_UNREACHABLE");

    openWorkspace.mockResolvedValue(SYNTHETIC_OPEN_RESPONSE);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.getByTestId("opened")).toHaveTextContent(
        "/abs/example/job-invoicing",
      ),
    );
  });

  it("retry after clearing the input is blocked by the same path guard", async () => {
    openWorkspace.mockRejectedValue(
      new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    );
    const user = userEvent.setup();
    renderHome();
    await user.click(
      screen.getByRole("button", { name: "Open workspace" }),
    );
    await screen.findByRole("button", { name: "Retry" });
    expect(openWorkspace).toHaveBeenCalledTimes(1);

    await user.clear(screen.getByLabelText("Workspace path"));
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(openWorkspace).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText("Enter a workspace path."),
    ).toBeInTheDocument();
  });

  it("404 is shown without implying retry fixes it", async () => {
    openWorkspace.mockRejectedValue(
      new ApiError("API.WORKSPACE_NOT_FOUND", "no such workspace", 404, false),
    );
    const user = userEvent.setup();
    renderHome();
    await user.click(
      screen.getByRole("button", { name: "Open workspace" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("API.WORKSPACE_NOT_FOUND");
    expect(
      screen.queryByRole("button", { name: "Retry" }),
    ).toBeNull();
  });
});
