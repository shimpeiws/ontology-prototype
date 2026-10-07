/**
 * DraftEditor tests — synthetic fixture mode (fixtures.ts).
 */

import { useEffect } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../lib/api";
import { DraftEditor } from "../components/DraftEditor";
import { useWorkspace, WorkspaceProvider } from "../workspace/context";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import type { WorkspaceState } from "../workspace/store";
import type {
  OpenResponse,
  SaveResponse,
  WorkspaceFileName,
} from "../lib/types";
import {
  SYNTHETIC_OPEN_RESPONSE,
  SYNTHETIC_SAVE_RESPONSE,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: {
      ...mod.api,
      saveWorkspace: vi.fn(),
    },
  };
});

const saveWorkspace = vi.mocked(api.saveWorkspace);

afterEach(() => {
  saveWorkspace.mockReset();
});

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

/** Renders the editor against a pre-built store state (e.g. a save in
 *  flight) — the Harness' open() would reset it. */
function renderEditorWithState(
  state: WorkspaceState,
  file: WorkspaceFileName = "domain.yaml",
) {
  return render(
    <WorkspaceProvider initial={state}>
      <DraftEditor file={file} />
    </WorkspaceProvider>,
  );
}

function dirtyEditorState(): WorkspaceState {
  const opened = workspaceReducer(initialWorkspaceState, {
    type: "opened",
    requestedPath: "job-invoicing",
    response: SYNTHETIC_OPEN_RESPONSE,
  });
  return workspaceReducer(opened, {
    type: "edited",
    file: "domain.yaml",
    text: "changed: true",
  });
}

/** Reverts a file through the store directly — exercises the "clean via
 *  another path" disarm without going through the editor's own confirm. */
function RevertProbe({ file }: { file: WorkspaceFileName }) {
  const { revertFile } = useWorkspace();
  return (
    <button
      type="button"
      data-testid={`revert-${file}`}
      onClick={() => revertFile(file)}
    />
  );
}

/** Triggers the real saveDraft path (api.saveWorkspace is mocked). */
function SaveProbe() {
  const { saveDraft } = useWorkspace();
  return (
    <button
      type="button"
      data-testid="save-draft"
      onClick={() => void saveDraft()}
    />
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

  it("editing shows unsaved badge and confirmed revert restores active text", async () => {
    const user = userEvent.setup();
    renderEditor();
    const textarea = screen.getByLabelText("domain.yaml draft YAML");

    await user.type(textarea, "# edit");
    expect(screen.getByText("unsaved")).toBeInTheDocument();
    const editedText = (textarea as HTMLTextAreaElement).value;

    // The first click only arms the confirmation — nothing is reverted.
    await user.click(
      screen.getByRole("button", { name: "Revert to active" }),
    );
    expect(
      screen.getByText(/Unsaved changes to.*will be discarded/),
    ).toBeInTheDocument();
    expect(textarea).toHaveValue(editedText);

    await user.click(
      screen.getByRole("button", { name: "Discard changes" }),
    );
    expect(textarea).toHaveValue(
      SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"],
    );
    expect(screen.queryByText("unsaved")).toBeNull();
  });

  it("cancelling the revert confirmation keeps the draft byte-for-byte", async () => {
    const user = userEvent.setup();
    renderEditor();
    const textarea = screen.getByLabelText("domain.yaml draft YAML");

    await user.type(textarea, "# edit");
    const editedText = (textarea as HTMLTextAreaElement).value;

    await user.click(
      screen.getByRole("button", { name: "Revert to active" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Keep editing" }),
    );

    expect(textarea).toHaveValue(editedText);
    expect(screen.getByText("unsaved")).toBeInTheDocument();
    expect(
      screen.queryByText(/Unsaved changes to.*will be discarded/),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Revert to active" }),
    ).toBeEnabled();
  });

  it("revert is disabled while a save is in flight", () => {
    // A save already sent cannot be recalled — discarding edits now would
    // let them still reach disk, so the discard path waits for it.
    const state = workspaceReducer(dirtyEditorState(), {
      type: "saveStarted",
      requestId: 1,
    });
    renderEditorWithState(state);
    expect(
      screen.getByRole("button", { name: "Revert to active" }),
    ).toBeDisabled();
  });

  it("Remove from draft is disabled while a save is in flight", () => {
    const state = workspaceReducer(dirtyEditorState(), {
      type: "saveStarted",
      requestId: 1,
    });
    renderEditorWithState(state);
    expect(
      screen.getByRole("button", { name: "Remove from draft" }),
    ).toBeDisabled();
  });

  it("clears the armed confirmation when the file becomes clean another way", async () => {
    const user = userEvent.setup();
    render(
      <WorkspaceProvider>
        <Harness>
          <DraftEditor file="domain.yaml" />
          <RevertProbe file="domain.yaml" />
        </Harness>
      </WorkspaceProvider>,
    );
    const textarea = screen.getByLabelText("domain.yaml draft YAML");
    await user.type(textarea, "# edit");

    await user.click(
      screen.getByRole("button", { name: "Revert to active" }),
    );
    expect(
      screen.getByRole("button", { name: "Discard changes" }),
    ).toBeInTheDocument();

    // The file became clean via a path other than this confirm (here a
    // store-level revert — in the app, e.g. Revert all). The arm must not
    // stay around to fire later against a different baseline.
    await user.click(screen.getByTestId("revert-domain.yaml"));
    expect(
      screen.queryByRole("button", { name: "Discard changes" }),
    ).toBeNull();
    expect(
      screen.queryByText(/will be discarded/),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Revert to active" }),
    ).toBeDisabled();
  });

  it("an armed confirmation does not reappear after a save settles", async () => {
    let resolveSave!: (r: SaveResponse) => void;
    saveWorkspace.mockReturnValue(
      new Promise<SaveResponse>((r) => {
        resolveSave = r;
      }),
    );
    const user = userEvent.setup();
    render(
      <WorkspaceProvider>
        <Harness>
          <DraftEditor file="domain.yaml" />
          <SaveProbe />
        </Harness>
      </WorkspaceProvider>,
    );
    const textarea = screen.getByLabelText("domain.yaml draft YAML");
    await user.type(textarea, "# edit");

    await user.click(
      screen.getByRole("button", { name: "Revert to active" }),
    );
    expect(
      screen.getByRole("button", { name: "Discard changes" }),
    ).toBeInTheDocument();

    // The save disarms the pending confirmation: the baseline it described
    // may move when the save lands, so it must not pop back on its own.
    await user.click(screen.getByTestId("save-draft"));
    expect(
      screen.queryByRole("button", { name: "Discard changes" }),
    ).toBeNull();

    // A conflicted save leaves the draft dirty — but the confirm stays
    // disarmed; reverting now would target the post-save baseline, not
    // the one the FDE armed against.
    resolveSave({
      ...SYNTHETIC_SAVE_RESPONSE,
      outcome: "conflict",
      digest: "on-disk-now",
      message: "workspace files changed on disk",
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Revert to active" }),
      ).toBeEnabled(),
    );
    expect(
      screen.queryByRole("button", { name: "Discard changes" }),
    ).toBeNull();
    expect(screen.getByText("unsaved")).toBeInTheDocument();
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
