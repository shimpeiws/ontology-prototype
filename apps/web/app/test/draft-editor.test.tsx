/**
 * DraftEditor tests — synthetic fixture mode (fixtures.ts).
 */

import { useEffect } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DraftEditor } from "../components/DraftEditor";
import { useWorkspace, WorkspaceProvider } from "../workspace/context";
import { SYNTHETIC_OPEN_RESPONSE } from "./fixtures";

function Harness({ children }: { children: React.ReactNode }) {
  const { state, open } = useWorkspace();
  useEffect(() => {
    open("job-invoicing", SYNTHETIC_OPEN_RESPONSE);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!state.workspace) return null;
  return <>{children}</>;
}

function renderEditor() {
  return render(
    <WorkspaceProvider>
      <Harness>
        <DraftEditor file="domain.yaml" />
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

  it("editing shows unsaved badge and revert restores active text", async () => {
    const user = userEvent.setup();
    renderEditor();
    const textarea = screen.getByLabelText("domain.yaml draft YAML");

    await user.type(textarea, "# edit");
    expect(screen.getByText("unsaved")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Revert to active" }),
    );
    expect(textarea).toHaveValue(
      SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"],
    );
    expect(screen.queryByText("unsaved")).toBeNull();
  });
});
