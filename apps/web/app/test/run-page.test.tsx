/**
 * Run page tests (#85–#89) — synthetic fixture mode (fixtures.ts). The
 * trigger calls and the overview/proposals reads are mocked; the page
 * renders inside a seeded WorkspaceProvider with a memory router (the
 * stage cards deep-link to other surfaces).
 *
 * Covers: the five-stage sequence display, last-run lines, the next-
 * action banner and its blocked states, every stage trigger's request
 * shape, in-place results, and the inline execute affordance.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../lib/api";
import type { EvaluationReport } from "../lib/types";
import RunPage from "../routes/workspace.run";
import { WorkspaceProvider } from "../workspace/context";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import type { WorkspaceState } from "../workspace/store";
import {
  SYNTHETIC_CONTRACT_RESPONSE,
  SYNTHETIC_OPEN_RESPONSE,
  SYNTHETIC_PROPOSAL_OPEN,
  SYNTHETIC_PROPOSALS_AFTER_APPROVE,
  SYNTHETIC_PROPOSALS_EMPTY,
  SYNTHETIC_PROPOSALS_RESPONSE,
  SYNTHETIC_TRACE_OVERVIEW,
  SYNTHETIC_TRACE_OVERVIEW_EMPTY,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: {
      ...mod.api,
      contract: vi.fn(),
      traceOverview: vi.fn(),
      proposals: vi.fn(),
      ingest: vi.fn(),
      project: vi.fn(),
      diagnose: vi.fn(),
      evaluate: vi.fn(),
      execute: vi.fn(),
    },
  };
});

const contract = vi.mocked(api.contract);
const traceOverview = vi.mocked(api.traceOverview);
const proposals = vi.mocked(api.proposals);
const ingest = vi.mocked(api.ingest);
const project = vi.mocked(api.project);
const diagnose = vi.mocked(api.diagnose);
const evaluate = vi.mocked(api.evaluate);
const execute = vi.mocked(api.execute);

function cleanState(): WorkspaceState {
  return workspaceReducer(initialWorkspaceState, {
    type: "opened",
    requestedPath: "job-invoicing",
    response: SYNTHETIC_OPEN_RESPONSE,
  });
}

function renderPage(state: WorkspaceState = cleanState()) {
  render(
    <MemoryRouter>
      <WorkspaceProvider initial={state}>
        <RunPage />
      </WorkspaceProvider>
    </MemoryRouter>,
  );
}

const mocked = [
  contract,
  traceOverview,
  proposals,
  ingest,
  project,
  diagnose,
  evaluate,
  execute,
];

afterEach(() => {
  mocked.forEach((m) => m.mockReset());
});

/** Wire the standard "everything loaded" mocks. */
function ready(
  trace = SYNTHETIC_TRACE_OVERVIEW,
  proposalList = SYNTHETIC_PROPOSALS_RESPONSE,
) {
  contract.mockResolvedValue(SYNTHETIC_CONTRACT_RESPONSE);
  traceOverview.mockResolvedValue(trace);
  proposals.mockResolvedValue(proposalList);
}

describe("run page", () => {
  it("renders the five stages in order with the next action", async () => {
    ready();
    renderPage();

    const banner = await screen.findByLabelText("next available action");
    expect(banner).toHaveTextContent("Review the open proposals");
    const stages = within(
      screen.getByRole("list", { name: "loop stages" }),
    ).getAllByRole("listitem");
    const labels = stages.map((s) =>
      s.getAttribute("aria-label") ?? "",
    );
    expect(labels).toEqual([
      "stage 1 ingest",
      "stage 2 project",
      "stage 3 diagnose",
      "stage 4 review and execute",
      "stage 5 evaluate",
    ]);
    // Last-run lines come from the shared trace view.
    expect(
      screen.getByText("ing-20261005T120000000000Z-aaaa0000"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("prj-20261005T120500000000Z-bbbb1111"),
    ).toBeInTheDocument();
  });

  it("starts the sequence at ingest when nothing is recorded", async () => {
    ready(SYNTHETIC_TRACE_OVERVIEW_EMPTY, SYNTHETIC_PROPOSALS_EMPTY);
    renderPage();
    const banner = await screen.findByLabelText("next available action");
    expect(banner).toHaveTextContent("Ingest a source snapshot");
    expect(
      within(banner).getByRole("button", { name: "Refresh state" }),
    ).toBeEnabled();
  });

  it("blocks on unapproved proposals with a review deep link", async () => {
    ready(SYNTHETIC_TRACE_OVERVIEW, {
      ...SYNTHETIC_PROPOSALS_RESPONSE,
      proposals: [SYNTHETIC_PROPOSAL_OPEN],
    });
    renderPage();
    const banner = await screen.findByLabelText("next available action");
    expect(banner).toHaveTextContent("Review the open proposals");
    const link = within(banner).getByRole("link", { name: "open" });
    expect(link).toHaveAttribute("href", "/workspace/proposals");
    const stage = screen.getByLabelText("stage 4 review and execute");
    expect(stage).toHaveTextContent(/await a human decision/);
  });

  it("offers inline execution for approved unexecuted proposals", async () => {
    ready(SYNTHETIC_TRACE_OVERVIEW, SYNTHETIC_PROPOSALS_AFTER_APPROVE);
    renderPage();
    const stage = await screen.findByLabelText(
      "stage 4 review and execute",
    );
    // The fixture's approved proposal carries no execution.
    const control = within(stage).getByRole("button", {
      name: "Execute proposal",
    });
    expect(control).toBeEnabled();
    // Armed confirmation before any call.
    const user = userEvent.setup();
    await user.click(control);
    expect(execute).not.toHaveBeenCalled();
    expect(
      within(stage).getByRole("button", { name: "Confirm write-back" }),
    ).toBeInTheDocument();
  });

  it("ingests a snapshot through the trigger and shows the result", async () => {
    ready(SYNTHETIC_TRACE_OVERVIEW_EMPTY, SYNTHETIC_PROPOSALS_EMPTY);
    ingest.mockResolvedValue({
      api_version: "v1",
      workspace: "job-invoicing",
      snapshot: "jobs.csv",
      ok: true,
      ingest_id: "ing-new",
      accepted: 3,
      rejected: 0,
      canonical: {
        entities_upserted: 3,
        events_appended: 3,
        events_reobserved: 0,
        fact_links: [],
      },
      evidence: { snapshot: "/w/.ontology/snapshots/x", observations: "/w/.ontology/observations/y" },
      diagnostics: [],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("stage 1 ingest");

    await user.type(
      screen.getByLabelText("Snapshot text"),
      "id,status\n101,done",
    );
    await user.click(
      screen.getByRole("button", { name: "Ingest snapshot" }),
    );

    await waitFor(() =>
      expect(ingest).toHaveBeenCalledWith("job-invoicing", {
        text: "id,status\n101,done",
        name: undefined,
        source: "jobs",
      }),
    );
    const stage = screen.getByLabelText("stage 1 ingest");
    await within(stage).findByText(/ing-new/);
    // A successful trigger refreshes the loop state.
    await waitFor(() =>
      expect(traceOverview.mock.calls.length).toBeGreaterThan(1),
    );
  });

  it("renders ingest row rejections as diagnostics in place", async () => {
    ready(SYNTHETIC_TRACE_OVERVIEW_EMPTY, SYNTHETIC_PROPOSALS_EMPTY);
    ingest.mockResolvedValue({
      api_version: "v1",
      workspace: "job-invoicing",
      snapshot: "rows.csv",
      ok: false,
      accepted: 1,
      rejected: 1,
      diagnostics: [
        {
          severity: "error",
          code: "INGEST.MISSING_ID",
          message: "entity 'Job' resolved no id",
          file: "rows.csv",
          path: "record[2].id",
          line: null,
          hint: null,
        },
      ],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("stage 1 ingest");
    await user.type(screen.getByLabelText("Snapshot text"), "x");
    await user.click(
      screen.getByRole("button", { name: "Ingest snapshot" }),
    );
    const stage = screen.getByLabelText("stage 1 ingest");
    await within(stage).findByText("INGEST.MISSING_ID");
    // A domain outcome does not advance the loop state.
    expect(traceOverview).toHaveBeenCalledTimes(1);
  });

  it("runs a projection and lists the candidates", async () => {
    ready();
    project.mockResolvedValue({
      api_version: "v1",
      workspace: "job-invoicing",
      ok: true,
      run_id: "prj-new",
      projection: "CompletedButNotInvoiced",
      as_of: "2026-10-21T00:00:00Z",
      definition_digest: "sha256:x",
      recorded: true,
      candidates: [
        {
          subject: { entity: "Job", id: "C01" },
          occurred_at: "2026-10-17T00:00:00Z",
          elapsed_seconds: 345600,
          elapsed_days: 4,
          properties: {},
          property_types: {},
          evidence: {},
        },
      ],
      inputs: [{}],
      diagnostics: [],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("stage 2 project");
    await user.type(
      screen.getByLabelText("Projection as-of (required)"),
      "2026-10-21T00:00:00Z",
    );
    await user.click(
      screen.getByRole("button", { name: "Run projection" }),
    );
    await waitFor(() =>
      expect(project).toHaveBeenCalledWith(
        "job-invoicing",
        "2026-10-21T00:00:00Z",
        undefined,
      ),
    );
    const stage = screen.getByLabelText("stage 2 project");
    await within(stage).findByText(/prj-new/);
    expect(within(stage).getByText(/Job\/C01/)).toBeInTheDocument();
  });

  it("runs a stub diagnosis against the latest projection run", async () => {
    ready();
    diagnose.mockResolvedValue({
      api_version: "v1",
      workspace: "job-invoicing",
      ok: true,
      run_id: "dgx-new",
      projection_run_id: "prj-20261005T120500000000Z-bbbb1111",
      provider: "stub",
      model: "stub-offline",
      status: "ok",
      candidate_count: 2,
      deliverables: {},
      proposals: [{ action: "SendReminder" }],
      rejected: [],
      recorded: true,
      diagnostics: [],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("stage 3 diagnose");

    // ADR 0004: the console defaults to the credential-free stub; a
    // billed provider is always an explicit choice.
    expect(screen.getByLabelText("Provider")).toHaveValue("stub");
    await user.click(
      screen.getByRole("button", { name: "Run diagnosis" }),
    );
    await waitFor(() =>
      expect(diagnose).toHaveBeenCalledWith(
        "job-invoicing",
        { runId: "prj-20261005T120500000000Z-bbbb1111" },
        { provider: "stub", model: undefined },
      ),
    );
    const stage = screen.getByLabelText("stage 3 diagnose");
    await within(stage).findByText(/dgx-new/);
    expect(within(stage).getByText("review them")).toBeInTheDocument();
  });

  it("runs an evaluation and links the frozen report", async () => {
    ready();
    evaluate.mockResolvedValue({
      api_version: "v1",
      workspace: "job-invoicing",
      ok: true,
      run_id: "evl-new",
      evaluation: "completion-to-invoice-latency",
      as_of: "2026-10-26T00:00:00Z",
      boundary: "2026-10-15T00:00:00Z",
      contract_digest: "sha256:c",
      recorded: true,
      report: {
        overall: { status: "success" },
      } as unknown as EvaluationReport,
      diagnostics: [],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("stage 5 evaluate");
    await user.type(
      screen.getByLabelText("Boundary (required)"),
      "2026-10-15T00:00:00Z",
    );
    await user.type(
      screen.getByLabelText("Evaluation as-of (required)"),
      "2026-10-26T00:00:00Z",
    );
    await user.click(
      screen.getByRole("button", { name: "Run evaluation" }),
    );
    await waitFor(() =>
      expect(evaluate).toHaveBeenCalledWith(
        "job-invoicing",
        "2026-10-15T00:00:00Z",
        "2026-10-26T00:00:00Z",
      ),
    );
    const stage = screen.getByLabelText("stage 5 evaluate");
    await within(stage).findByText(/evl-new/);
    expect(
      within(stage).getByRole("link", { name: "open the report" }),
    ).toHaveAttribute("href", "/workspace/reports");
  });

  it("surfaces a transport failure as a retryable error", async () => {
    ready(SYNTHETIC_TRACE_OVERVIEW_EMPTY, SYNTHETIC_PROPOSALS_EMPTY);
    const { ApiError } = await import("../lib/api");
    ingest
      .mockRejectedValueOnce(
        new ApiError("STORAGE.WRITE_BUSY", "database is locked", 503, true),
      )
      .mockResolvedValueOnce({
        api_version: "v1",
        workspace: "job-invoicing",
        ok: true,
        ingest_id: "ing-retry",
        diagnostics: [],
      });
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("stage 1 ingest");
    await user.type(screen.getByLabelText("Snapshot text"), "x");
    await user.click(
      screen.getByRole("button", { name: "Ingest snapshot" }),
    );
    const stage = screen.getByLabelText("stage 1 ingest");
    const alert = await within(stage).findByRole("alert");
    expect(alert).toHaveTextContent("STORAGE.WRITE_BUSY");
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await within(stage).findByText(/ing-retry/);
  });
});
