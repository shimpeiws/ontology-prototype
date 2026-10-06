/**
 * Domain page tests — synthetic fixture mode (fixtures.ts).
 *
 * These run the real `DomainPage` route against a seeded workspace draft:
 * the graph, list, and forms all render the same draft text (INV-003),
 * edits land only in the draft layer (INV-001), and rename/remove go
 * through the reference-impact panel (INV-005).
 */

import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../lib/api";
import DomainPage from "../routes/workspace.domain";
import { useWorkspace, WorkspaceProvider } from "../workspace/context";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import type { OpenResponse } from "../lib/types";
import {
  SYNTHETIC_BUNDLE_OPEN_RESPONSE,
  SYNTHETIC_CONTRACT,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return { ...mod, api: { ...mod.api, contract: vi.fn() } };
});

const contract = vi.mocked(api.contract);

/** Renders the raw drafts so tests can assert cross-file edits. */
function DraftProbe() {
  const { state } = useWorkspace();
  return (
    <output data-testid="draft-probe">{JSON.stringify(state.drafts)}</output>
  );
}

function drafts(): Record<string, string | null> {
  return JSON.parse(
    screen.getByTestId("draft-probe").textContent ?? "{}",
  ) as Record<string, string | null>;
}

function renderPage(response: OpenResponse = SYNTHETIC_BUNDLE_OPEN_RESPONSE) {
  const state = workspaceReducer(initialWorkspaceState, {
    type: "opened",
    requestedPath: "job-invoicing",
    response,
  });
  return render(
    <WorkspaceProvider initial={state}>
      <DomainPage />
      <DraftProbe />
    </WorkspaceProvider>,
  );
}

afterEach(() => {
  contract.mockReset();
});

describe("domain page", () => {
  it("renders graph, list, and metadata from the same draft", async () => {
    contract.mockResolvedValue(SYNTHETIC_CONTRACT);
    renderPage();

    await waitFor(() => expect(contract).toHaveBeenCalled());
    // Graph cards (accessible buttons) and the inspectable list.
    expect(
      screen.getByRole("button", { name: "Entity Job" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Event JobCompleted" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Projection CompletedButNotInvoiced",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Person" }),
    ).toBeInTheDocument();
    // domain.name field carries the draft value.
    expect(
      screen.getByLabelText("name", { selector: "#domain-name-input" }),
    ).toHaveValue("job-invoicing");
    // The undeclared `Invoice` ref target renders as a ghost card.
    expect(screen.getByText("(not declared)")).toBeInTheDocument();
    expect(
      screen.getByTitle(
        "Invoice is referenced but not declared as an entity",
      ),
    ).toBeInTheDocument();
  });

  it("selecting a graph card opens its form; edits stay in the draft", async () => {
    contract.mockResolvedValue(SYNTHETIC_CONTRACT);
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "Entity Job" }));
    expect(
      screen.getByRole("heading", { name: "Edit entity" }),
    ).toBeInTheDocument();
    // The property type vocabulary comes from the contract.
    const typeSelect = await screen.findByLabelText("type of status");
    expect(typeSelect).toHaveValue("string");

    await user.selectOptions(typeSelect, "integer");
    expect(drafts()["domain.yaml"]).toContain("type: integer");
    // Draft-only: the textarea mirrors the same text and the badge shows.
    expect(
      screen.getByLabelText("domain.yaml draft YAML"),
    ).toHaveValue(drafts()["domain.yaml"]);
    expect(screen.getByText("unsaved")).toBeInTheDocument();
  });

  it("editing a ref target writes the draft text, not the active bundle", async () => {
    contract.mockResolvedValue(SYNTHETIC_CONTRACT);
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "Entity Job" }));
    const refSelect = await screen.findByLabelText("ref target of assignee");
    expect(refSelect).toHaveValue("Person");
    await user.selectOptions(refSelect, "Job");
    expect(drafts()["domain.yaml"]).toContain("ref: Job");
  });

  it("rename shows the reference impact and updates the bundle on confirm", async () => {
    contract.mockResolvedValue(SYNTHETIC_CONTRACT);
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "Person" }));
    await user.click(
      screen.getAllByRole("button", { name: "Rename…" })[0],
    );
    const input = screen.getByLabelText("New entity name");
    await user.clear(input);
    await user.type(input, "Customer");
    await user.click(
      screen.getByRole("button", { name: "Apply rename" }),
    );

    const panel = await screen.findByRole("alertdialog");
    expect(panel).toHaveTextContent("mapping.entities[1].entity");
    expect(panel).toHaveTextContent(
      "agent.permitted_actions.SendReminder.params.target.ref",
    );
    await user.click(
      screen.getByRole("button", { name: "Apply rename" }),
    );

    const files = drafts();
    expect(files["domain.yaml"]).toContain("Customer:");
    expect(files["domain.yaml"]).not.toContain("Person");
    expect(files["mapping.yaml"]).toContain("entity: Customer");
    expect(files["agent.yaml"]).toContain("ref: Customer");
    expect(files["evaluation.yaml"]).toBe(
      SYNTHETIC_BUNDLE_OPEN_RESPONSE.files["evaluation.yaml"],
    );
  });

  it("removal reports dangling references and leaves them for validation", async () => {
    contract.mockResolvedValue(SYNTHETIC_CONTRACT);
    const user = userEvent.setup();
    renderPage();

    await user.click(
      screen.getByRole("button", { name: "Event JobCompleted" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Remove event" }),
    );
    const panel = await screen.findByRole("alertdialog");
    expect(panel).toHaveTextContent("mapping.events[0].event");
    expect(panel).toHaveTextContent("evaluation.interval.start_event");
    expect(panel).toHaveTextContent("dangling");

    await user.click(screen.getByRole("button", { name: "Remove" }));
    const files = drafts();
    expect(files["domain.yaml"]).not.toContain("JobCompleted");
    // References stay broken until the FDE fixes them — the validator
    // reports them.
    expect(files["mapping.yaml"]).toContain("event: JobCompleted");
    expect(files["evaluation.yaml"]).toContain("start_event: JobCompleted");
  });

  it("cancel on the impact panel changes nothing", async () => {
    contract.mockResolvedValue(SYNTHETIC_CONTRACT);
    const user = userEvent.setup();
    renderPage();

    await user.click(
      screen.getByRole("button", { name: "Event InvoiceIssued" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Remove event" }),
    );
    await screen.findByRole("alertdialog");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(drafts()["domain.yaml"]).toBe(
      SYNTHETIC_BUNDLE_OPEN_RESPONSE.files["domain.yaml"],
    );
  });

  it("field-level diagnostics render beside the matching control", async () => {
    contract.mockResolvedValue(SYNTHETIC_CONTRACT);
    const response: OpenResponse = {
      ...SYNTHETIC_BUNDLE_OPEN_RESPONSE,
      diagnostics: [
        {
          severity: "error",
          code: "DOMAIN.UNKNOWN_ENTITY",
          message: "assignee points at nobody",
          file: "domain.yaml",
          path: "domain.entities.Job.properties.assignee.ref",
          line: 17,
          hint: "Declare it or fix the name.",
        },
      ],
    };
    const user = userEvent.setup();
    const { container } = renderPage(response);

    await user.click(screen.getByRole("button", { name: "Entity Job" }));
    const fieldList = await waitFor(() => {
      const el = container.querySelector(".field-diagnostics");
      expect(el?.textContent).toContain("assignee points at nobody");
      expect(el?.textContent).toContain("DOMAIN.UNKNOWN_ENTITY");
      expect(el?.textContent).toContain("Declare it or fix the name.");
      return el;
    });
    expect(fieldList).toBeInTheDocument();
  });

  it("a broken draft disables the structured editor but keeps the raw one", async () => {
    contract.mockResolvedValue(SYNTHETIC_CONTRACT);
    renderPage();
    const textarea = screen.getByLabelText("domain.yaml draft YAML");

    fireEvent.change(textarea, {
      target: { value: "domain: [unclosed" },
    });
    expect(
      await screen.findByText(/Structured editor unavailable/),
    ).toBeInTheDocument();
    expect(textarea).toHaveValue("domain: [unclosed");
  });

  it("contract fetch failure degrades type selects but keeps editing", async () => {
    contract.mockRejectedValue(
      new ApiError("WEB.API_UNREACHABLE", "API is down", null, true),
    );
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "WEB.API_UNREACHABLE",
    );
    // The structured editor still renders; type shows as read-only text.
    await user.click(screen.getByRole("button", { name: "Entity Job" }));
    expect(screen.queryByLabelText("type of status")).toBeNull();
    expect(
      screen.getAllByText("string", { selector: "code" }).length,
    ).toBeGreaterThan(0);
    // A different edit path (description) still works.
    const desc = screen.getByLabelText("description", {
      selector: "#entity-Job-desc",
    });
    fireEvent.change(desc, { target: { value: "work unit" } });
    expect(drafts()["domain.yaml"]).toContain("work unit");
  });
});
