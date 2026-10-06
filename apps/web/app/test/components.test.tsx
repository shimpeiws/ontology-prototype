/**
 * Shared-component tests — synthetic fixture mode (fixtures.ts).
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ApiError } from "../lib/api";
import { DiagnosticsList } from "../components/DiagnosticsList";
import {
  DraftStatusBadge,
  FileStatusList,
} from "../components/DraftStatus";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import { SYNTHETIC_DIAGNOSTIC, SYNTHETIC_OPEN_RESPONSE } from "./fixtures";

function openedState() {
  return workspaceReducer(initialWorkspaceState, {
    type: "opened",
    requestedPath: "job-invoicing",
    response: SYNTHETIC_OPEN_RESPONSE,
  });
}

describe("shared states", () => {
  it("loading state is announced politely", () => {
    render(<LoadingState label="Opening workspace…" />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Opening workspace…",
    );
  });

  it("error state shows the API code and retry only when retryable", () => {
    const onRetry = () => {};
    const { unmount } = render(
      <ErrorState
        error={new ApiError("WEB.API_UNREACHABLE", "down", null, true)}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "WEB.API_UNREACHABLE",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    unmount();

    render(
      <ErrorState
        error={new ApiError("API.WORKSPACE_NOT_FOUND", "gone", 404, false)}
        onRetry={onRetry}
      />,
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("empty state renders an actionable title", () => {
    render(<EmptyState title="No entities defined yet." />);
    expect(screen.getByText("No entities defined yet.")).toBeInTheDocument();
  });
});

describe("DiagnosticsList", () => {
  it("renders API diagnostics verbatim including file/path/hint", () => {
    render(<DiagnosticsList diagnostics={[SYNTHETIC_DIAGNOSTIC]} />);
    expect(screen.getByText("WORKSPACE.EXAMPLE")).toBeInTheDocument();
    expect(
      screen.getByText("Synthetic diagnostic for tests only."),
    ).toBeInTheDocument();
    expect(screen.getByText(/domain\.yaml/)).toBeInTheDocument();
    expect(screen.getByText(/domain\.entities\.Job/)).toBeInTheDocument();
    expect(screen.getByText("Synthetic hint.")).toBeInTheDocument();
  });

  it("filters diagnostics to one file and shows empty state", () => {
    render(
      <DiagnosticsList
        diagnostics={[SYNTHETIC_DIAGNOSTIC]}
        file="mapping.yaml"
      />,
    );
    expect(screen.getByText("No diagnostics.")).toBeInTheDocument();
  });

  it("keeps bundle-level (file: null) diagnostics visible on file pages", () => {
    const bundleLevel = {
      ...SYNTHETIC_DIAGNOSTIC,
      code: "WORKSPACE.NOT_A_DIRECTORY",
      file: null,
      path: null,
      line: null,
      hint: null,
    };
    render(
      <DiagnosticsList
        diagnostics={[SYNTHETIC_DIAGNOSTIC, bundleLevel]}
        file="mapping.yaml"
      />,
    );
    expect(
      screen.getByText("WORKSPACE.NOT_A_DIRECTORY"),
    ).toBeInTheDocument();
    expect(screen.queryByText("WORKSPACE.EXAMPLE")).toBeNull();
  });
});

describe("draft status", () => {
  it("shows active when clean and unsaved draft after an edit", () => {
    const state = openedState();
    const { rerender } = render(<DraftStatusBadge state={state} />);
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.queryByText("unsaved draft")).toBeNull();

    const edited = workspaceReducer(state, {
      type: "edited",
      file: "domain.yaml",
      text: "x",
    });
    rerender(<DraftStatusBadge state={edited} />);
    expect(screen.getByText("unsaved draft")).toBeInTheDocument();
  });

  it("per-file status marks missing files", () => {
    const missingAgent = {
      ...SYNTHETIC_OPEN_RESPONSE,
      files: { ...SYNTHETIC_OPEN_RESPONSE.files },
    };
    delete missingAgent.files["agent.yaml"];
    const state = workspaceReducer(initialWorkspaceState, {
      type: "opened",
      requestedPath: "x",
      response: missingAgent,
    });
    render(<FileStatusList state={state} />);
    const agentRow = screen.getByText("agent.yaml").closest("li");
    expect(agentRow).toHaveTextContent("missing");
  });

  it("per-file status shows unsaved for a missing file with draft text", () => {
    const missingAgent = {
      ...SYNTHETIC_OPEN_RESPONSE,
      files: { ...SYNTHETIC_OPEN_RESPONSE.files },
    };
    delete missingAgent.files["agent.yaml"];
    const opened = workspaceReducer(initialWorkspaceState, {
      type: "opened",
      requestedPath: "x",
      response: missingAgent,
    });
    const edited = workspaceReducer(opened, {
      type: "edited",
      file: "agent.yaml",
      text: "task: new",
    });
    render(<FileStatusList state={edited} />);
    const agentRow = screen.getByText("agent.yaml").closest("li");
    expect(agentRow).toHaveTextContent("unsaved");
    expect(agentRow).not.toHaveTextContent("missing");
  });
});

describe("keyboard operability", () => {
  it("error retry button is focusable and activates via keyboard", async () => {
    const user = userEvent.setup();
    let retried = false;
    render(
      <ErrorState
        error={new ApiError("WEB.API_UNREACHABLE", "down", null, true)}
        onRetry={() => {
          retried = true;
        }}
      />,
    );
    await user.tab();
    expect(screen.getByRole("button", { name: "Retry" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(retried).toBe(true);
  });
});
