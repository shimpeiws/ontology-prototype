/**
 * DraftEditor tests — synthetic fixture mode (fixtures.ts).
 */

import { useEffect } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DraftEditor } from "../components/DraftEditor";
import { useWorkspace, WorkspaceProvider } from "../workspace/context";
import { initialWorkspaceState, workspaceReducer } from "../workspace/store";
import type { OpenResponse, WorkspaceFileName } from "../lib/types";
import { SYNTHETIC_OPEN_RESPONSE } from "./fixtures";

function Harness({
  children,
  response = SYNTHETIC_OPEN_RESPONSE,
}: {
  children: React.ReactNode;
  response?: OpenResponse;
}) {
  const { state, open } = useWorkspace();
  useEffect(() => {
    open("job-invoicing", response);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!state.workspace) return null;
  return <>{children}</>;
}

function renderEditor(
  file: WorkspaceFileName = "domain.yaml",
  response?: OpenResponse,
) {
  return render(
    <WorkspaceProvider>
      <Harness response={response}>
        <DraftEditor file={file} />
      </Harness>
    </WorkspaceProvider>,
  );
}

describe("DraftEditor", () => {
  it("starts with the active file text and is labeled", () => {
    renderEditor();
    const textarea = screen.getByLabelText("domain.yaml draft YAML");
    expect(textarea).toHaveValue(
      SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"],
    );
  });

  it("editing shows unsaved badge and a confirmed revert restores active text", async () => {
    const user = userEvent.setup();
    renderEditor();
    const textarea = screen.getByLabelText("domain.yaml draft YAML");

    await user.type(textarea, "# edit");
    expect(screen.getByText("unsaved")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Revert to active" }),
    );
    // The first click only asks; nothing is discarded yet.
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Discard unsaved changes to domain.yaml and restore the active text?",
    );
    expect(textarea).toHaveValue(
      `${SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"]}# edit`,
    );

    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(textarea).toHaveValue(
      SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"],
    );
    expect(screen.queryByText("unsaved")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keep editing cancels the revert and keeps the draft", async () => {
    const user = userEvent.setup();
    renderEditor();
    const textarea = screen.getByLabelText("domain.yaml draft YAML");

    await user.type(textarea, "# edit");
    await user.click(
      screen.getByRole("button", { name: "Revert to active" }),
    );
    await user.click(screen.getByRole("button", { name: "Keep editing" }));

    expect(screen.queryByRole("alert")).toBeNull();
    expect(textarea).toHaveValue(
      `${SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"]}# edit`,
    );
    expect(screen.getByText("unsaved")).toBeInTheDocument();
  });

  it("revert stays disabled while a save is in flight", () => {
    let state = workspaceReducer(initialWorkspaceState, {
      type: "opened",
      requestedPath: "job-invoicing",
      response: SYNTHETIC_OPEN_RESPONSE,
    });
    state = workspaceReducer(state, {
      type: "edited",
      file: "domain.yaml",
      text: "changed: true",
    });
    state = workspaceReducer(state, { type: "saveStarted", requestId: 1 });
    render(
      <WorkspaceProvider initial={state}>
        <DraftEditor file="domain.yaml" />
      </WorkspaceProvider>,
    );
    expect(
      screen.getByRole("button", { name: "Revert to active" }),
    ).toBeDisabled();
  });

  it("removing an on-disk file from the draft says 'removed from draft'", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(
      screen.getByRole("button", { name: "Remove from draft" }),
    );
    expect(screen.getByText("removed from draft")).toBeInTheDocument();
    expect(screen.queryByText("not on disk")).toBeNull();
  });

  it("a file absent on disk says 'not on disk'", () => {
    const missingAgent: OpenResponse = {
      ...SYNTHETIC_OPEN_RESPONSE,
      files: { ...SYNTHETIC_OPEN_RESPONSE.files },
    };
    delete missingAgent.files["agent.yaml"];
    renderEditor("agent.yaml", missingAgent);
    expect(screen.getByText("not on disk")).toBeInTheDocument();
    expect(
      screen.queryByText("removed from draft"),
    ).toBeNull();
  });
});
