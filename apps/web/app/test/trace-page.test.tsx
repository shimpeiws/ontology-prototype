/**
 * Trace page tests (#62) — synthetic fixture mode (fixtures.ts), shaped
 * after the #61 operational contract. `api.trace*` is mocked; the page
 * renders inside a seeded WorkspaceProvider.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../lib/api";
import type { TraceViewResponse } from "../lib/types";
import TracePage from "../routes/workspace.trace";
import { WorkspaceProvider } from "../workspace/context";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import type { WorkspaceState } from "../workspace/store";
import {
  SYNTHETIC_OPEN_RESPONSE,
  SYNTHETIC_TRACE_INVALID_SUBJECT,
  SYNTHETIC_TRACE_OVERVIEW,
  SYNTHETIC_TRACE_OVERVIEW_EMPTY,
  SYNTHETIC_TRACE_RUN_RESPONSE,
  SYNTHETIC_TRACE_SUBJECT_RESPONSE,
  SYNTHETIC_TRACE_SUBJECT_WITH_WARNINGS,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: {
      ...mod.api,
      traceOverview: vi.fn(),
      traceSubject: vi.fn(),
      traceRun: vi.fn(),
    },
  };
});

const traceOverview = vi.mocked(api.traceOverview);
const traceSubject = vi.mocked(api.traceSubject);
const traceRun = vi.mocked(api.traceRun);

function cleanState(): WorkspaceState {
  return workspaceReducer(initialWorkspaceState, {
    type: "opened",
    requestedPath: "job-invoicing",
    response: SYNTHETIC_OPEN_RESPONSE,
  });
}

function renderPage(
  state: WorkspaceState = cleanState(),
  route = "/workspace/trace",
) {
  render(
    <MemoryRouter initialEntries={[route]}>
      <WorkspaceProvider initial={state}>
        <TracePage />
      </WorkspaceProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  traceOverview.mockReset();
  traceSubject.mockReset();
  traceRun.mockReset();
});

describe("trace page overview", () => {
  it("loads and renders the trace overview on mount", async () => {
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW);
    renderPage();

    await screen.findByText("Projection runs");
    expect(traceOverview).toHaveBeenCalledWith(
      "job-invoicing",
      expect.any(AbortSignal),
    );
    // The failed diagnosis run is evidence, not a gap.
    expect(
      screen.getByText("dgx-20261005T120700000000Z-dddd3333"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("failed").length).toBeGreaterThan(0);
    // The frozen evaluation's not_met verdict is styled as failure.
    const verdict = screen.getByText("not_met");
    expect(verdict.classList.contains("status-bad")).toBe(true);
    expect(verdict.classList.contains("status-ok")).toBe(false);
    // Unindexed proposals and unresolved links surface explicitly.
    expect(screen.getByText(/1 unindexed/)).toBeInTheDocument();
    expect(screen.getByText(/1 unresolved/)).toBeInTheDocument();
    // Journal corruption is a diagnostic, not a hidden gap.
    expect(screen.getByText("TRACE.JOURNAL_CORRUPT")).toBeInTheDocument();
  });

  it("renders the empty state for a workspace with no history", async () => {
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW_EMPTY);
    renderPage();
    await screen.findByText(/No recorded history/);
  });

  it("canonical facts alone count as recorded history", async () => {
    traceOverview.mockResolvedValue({
      ...SYNTHETIC_TRACE_OVERVIEW_EMPTY,
      canonical: { entities: 2, facts: 3, fact_links: 1 },
    });
    renderPage();

    await screen.findByRole("heading", { name: /Canonical/ });
    expect(screen.queryByText(/No recorded history/)).toBeNull();
  });

  it("renders the evidence counts when only snapshots exist", async () => {
    traceOverview.mockResolvedValue({
      ...SYNTHETIC_TRACE_OVERVIEW_EMPTY,
      evidence: { ingest_runs: [], observations: 0, snapshots: 1 },
    });
    renderPage();

    const heading = await screen.findByRole("heading", { name: /Evidence/ });
    const section = within(heading.closest("section") as HTMLElement);
    expect(section.getByText("Snapshots")).toBeInTheDocument();
    expect(section.queryByText(/No observations recorded/)).toBeNull();
  });

  it("renders the canonical counts when only entities exist", async () => {
    traceOverview.mockResolvedValue({
      ...SYNTHETIC_TRACE_OVERVIEW_EMPTY,
      canonical: { entities: 2, facts: 0, fact_links: 0 },
    });
    renderPage();

    const heading = await screen.findByRole("heading", { name: /Canonical/ });
    const section = within(heading.closest("section") as HTMLElement);
    expect(section.getByText("Entities")).toBeInTheDocument();
    expect(section.queryByText(/No canonical entities or facts/)).toBeNull();
  });

  it("shows a retryable error state when the API is unreachable", async () => {
    traceOverview
      .mockRejectedValueOnce(
        new ApiError("WEB.API_UNREACHABLE", "API is down", null, true),
      )
      .mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW);
    const user = userEvent.setup();
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("WEB.API_UNREACHABLE");

    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Projection runs");
    expect(traceOverview).toHaveBeenCalledTimes(2);
  });
});

describe("trace page subject lookup", () => {
  it("sends the subject verbatim and renders the chain in stage order", async () => {
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW);
    traceSubject.mockResolvedValue(SYNTHETIC_TRACE_SUBJECT_RESPONSE);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Projection runs");

    await user.type(screen.getByLabelText("Subject"), "Job/101");
    await user.click(
      screen.getByRole("button", { name: "Follow subject" }),
    );

    await waitFor(() =>
      expect(traceSubject).toHaveBeenCalledWith("job-invoicing", "Job/101"),
    );

    // The chain renders every stage — including the failed diagnosis run.
    const subjectView = document.querySelector(".trace-subject");
    expect(subjectView).not.toBeNull();
    const inTrace = within(subjectView as HTMLElement);
    for (const heading of [
      "Observations",
      "Facts",
      "Diagnosis runs",
      "Proposals",
      "Decisions",
      "Executions",
      "Evaluations",
    ]) {
      expect(inTrace.getByText(heading)).toBeInTheDocument();
    }
    expect(
      inTrace.getByText("dgx-20261005T120700000000Z-dddd3333"),
    ).toBeInTheDocument();
    // The unindexed proposal keeps its status visible.
    expect(
      screen.getByText("dgx-20261005T120600000000Z-cccc2222-pos1"),
    ).toBeInTheDocument();
    // Unresolved links surface instead of disappearing.
    expect(inTrace.getByText("dgx-missing")).toBeInTheDocument();
    // The failed journal event is flagged.
    expect(inTrace.getByText("diagnosis.failed")).toBeInTheDocument();
    // The write-back receipt renders alongside the execution status.
    expect(inTrace.getByText("receipt")).toBeInTheDocument();
    expect(inTrace.getByText(/reminders\.csv/)).toBeInTheDocument();
  });

  it("renders warning diagnostics alongside a successful trace", async () => {
    // ok:true only rules out error-severity diagnostics; warnings such as
    // TRACE.JOURNAL_CORRUPT are evidence and must not be dropped.
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW_EMPTY);
    traceSubject.mockResolvedValue(SYNTHETIC_TRACE_SUBJECT_WITH_WARNINGS);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/No recorded history/);

    await user.type(screen.getByLabelText("Subject"), "Job/101");
    await user.click(
      screen.getByRole("button", { name: "Follow subject" }),
    );

    await waitFor(() =>
      expect(document.querySelector(".trace-subject")).not.toBeNull(),
    );
    const lookupArea = document.querySelector("[aria-live=polite]");
    expect(
      within(lookupArea as HTMLElement).getByText("TRACE.JOURNAL_CORRUPT"),
    ).toBeInTheDocument();
  });

  it("renders domain failures as diagnostics, not transport errors", async () => {
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW);
    traceSubject.mockResolvedValue(SYNTHETIC_TRACE_INVALID_SUBJECT);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Projection runs");

    await user.type(screen.getByLabelText("Subject"), "bogus");
    await user.click(
      screen.getByRole("button", { name: "Follow subject" }),
    );

    await screen.findByText("TRACE.INVALID_SUBJECT");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a superseded lookup response never overwrites the newer one", async () => {
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW);
    let resolveSubject!: (r: TraceViewResponse) => void;
    traceSubject.mockReturnValue(
      new Promise<TraceViewResponse>((r) => {
        resolveSubject = r;
      }),
    );
    traceRun.mockResolvedValue(SYNTHETIC_TRACE_RUN_RESPONSE);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Projection runs");

    // The artifact lookup is submitted (via Enter) while the subject
    // request is still in flight.
    await user.type(screen.getByLabelText("Subject"), "Job/101");
    await user.click(
      screen.getByRole("button", { name: "Follow subject" }),
    );
    await user.type(
      screen.getByLabelText("Artifact id"),
      "dgx-20261005T120700000000Z-dddd3333",
    );
    await user.keyboard("{Enter}");
    await screen.findByText("diagnosis_run");

    // The earlier subject response lands late — it must be discarded.
    resolveSubject(SYNTHETIC_TRACE_SUBJECT_RESPONSE);
    await waitFor(() =>
      expect(document.querySelector(".trace-subject")).toBeNull(),
    );
    expect(document.querySelector(".trace-anchor")).not.toBeNull();
  });

  it("an artifact view offers subject pivots back into the chain", async () => {
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW);
    traceRun.mockResolvedValue(SYNTHETIC_TRACE_RUN_RESPONSE);
    traceSubject.mockResolvedValue(SYNTHETIC_TRACE_SUBJECT_RESPONSE);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Projection runs");

    await user.type(screen.getByLabelText("Artifact id"), "dgx-x");
    await user.click(
      screen.getByRole("button", { name: "Inspect artifact" }),
    );
    await screen.findByText("diagnosis_run");

    await user.click(screen.getByRole("button", { name: "Job/101" }));
    await waitFor(() =>
      expect(traceSubject).toHaveBeenCalledWith("job-invoicing", "Job/101"),
    );
    await waitFor(() =>
      expect(document.querySelector(".trace-subject")).not.toBeNull(),
    );
  });

  it("marks a shown result stale when the input changes", async () => {
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW);
    traceSubject.mockResolvedValue(SYNTHETIC_TRACE_SUBJECT_RESPONSE);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Projection runs");

    await user.type(screen.getByLabelText("Subject"), "Job/101");
    await user.click(
      screen.getByRole("button", { name: "Follow subject" }),
    );
    await waitFor(() =>
      expect(document.querySelector(".trace-subject")).not.toBeNull(),
    );
    expect(screen.queryByText("stale")).toBeNull();

    await user.type(screen.getByLabelText("Subject"), "2");
    await screen.findByText("stale");
  });
});

describe("trace page deep links", () => {
  it("?subject=Job/101 prefills and runs the subject lookup once", async () => {
    // #97: references on the proposal card land on this route; the
    // query runs the matching lookup, not just a prefill.
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW);
    traceSubject.mockResolvedValue(SYNTHETIC_TRACE_SUBJECT_RESPONSE);
    renderPage(cleanState(), "/workspace/trace?subject=Job%2F101");

    await waitFor(() =>
      expect(traceSubject).toHaveBeenCalledWith("job-invoicing", "Job/101"),
    );
    await waitFor(() =>
      expect(document.querySelector(".trace-subject")).not.toBeNull(),
    );
    expect(screen.getByLabelText("Subject")).toHaveValue("Job/101");
    expect(traceSubject).toHaveBeenCalledTimes(1);
    expect(traceRun).not.toHaveBeenCalled();
  });

  it("?artifact=<id> prefills and runs the artifact lookup once", async () => {
    traceOverview.mockResolvedValue(SYNTHETIC_TRACE_OVERVIEW);
    traceRun.mockResolvedValue(SYNTHETIC_TRACE_RUN_RESPONSE);
    renderPage(cleanState(), "/workspace/trace?artifact=dgx-x");

    await waitFor(() =>
      expect(traceRun).toHaveBeenCalledWith("job-invoicing", "dgx-x"),
    );
    await screen.findByText("diagnosis_run");
    expect(screen.getByLabelText("Artifact id")).toHaveValue("dgx-x");
    expect(traceRun).toHaveBeenCalledTimes(1);
    expect(traceSubject).not.toHaveBeenCalled();
  });
});
