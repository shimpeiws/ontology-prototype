/**
 * Evaluation reports page tests (#62) — synthetic fixture mode, shaped
 * after the #61 operational contract. `api.evaluation*` is mocked; the
 * page renders inside a seeded WorkspaceProvider.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../lib/api";
import ReportsPage from "../routes/workspace.reports";
import { WorkspaceProvider } from "../workspace/context";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import type { WorkspaceState } from "../workspace/store";
import {
  SYNTHETIC_EVALUATION_RUN_RESPONSE,
  SYNTHETIC_EVALUATION_RUN_UNVERIFIED,
  SYNTHETIC_EVALUATION_RUNS,
  SYNTHETIC_EVALUATION_RUNS_EMPTY,
  SYNTHETIC_EVALUATION_UNKNOWN_RUN,
  SYNTHETIC_OPEN_RESPONSE,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: {
      ...mod.api,
      evaluationRuns: vi.fn(),
      evaluationRun: vi.fn(),
    },
  };
});

const evaluationRuns = vi.mocked(api.evaluationRuns);
const evaluationRun = vi.mocked(api.evaluationRun);

function cleanState(): WorkspaceState {
  return workspaceReducer(initialWorkspaceState, {
    type: "opened",
    requestedPath: "job-invoicing",
    response: SYNTHETIC_OPEN_RESPONSE,
  });
}

function renderPage(state: WorkspaceState = cleanState()) {
  render(
    <WorkspaceProvider initial={state}>
      <ReportsPage />
    </WorkspaceProvider>,
  );
}

afterEach(() => {
  evaluationRuns.mockReset();
  evaluationRun.mockReset();
});

async function openReport(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("evl-20261015T000000000000Z-eeee4444");
  await user.click(
    screen.getByRole("button", {
      name: /evl-20261015T000000000000Z-eeee4444/,
    }),
  );
  await screen.findByText("Cohorts");
  return within(
    document.querySelector(".evaluation-report") as HTMLElement,
  );
}

describe("evaluation run list", () => {
  it("lists frozen runs with honest verdict styling", async () => {
    evaluationRuns.mockResolvedValue(SYNTHETIC_EVALUATION_RUNS);
    renderPage();

    await screen.findByText("evl-20261015T000000000000Z-eeee4444");
    expect(evaluationRuns).toHaveBeenCalledWith(
      "job-invoicing",
      expect.any(AbortSignal),
    );

    // AC: not_met / unverified must never look like success.
    const notMet = screen.getByText("not_met");
    expect(notMet.className).toContain("status-bad");
    expect(notMet.className).not.toContain("status-ok");
    const unverified = screen.getByText("unverified");
    expect(unverified.className).toContain("status-unknown");
    expect(unverified.className).not.toContain("status-ok");
  });

  it("renders the empty state when no runs are frozen", async () => {
    evaluationRuns.mockResolvedValue(SYNTHETIC_EVALUATION_RUNS_EMPTY);
    renderPage();
    await screen.findByText(/No frozen evaluation reports/);
  });

  it("shows a retryable error state when the API is unreachable", async () => {
    evaluationRuns
      .mockRejectedValueOnce(
        new ApiError("WEB.API_UNREACHABLE", "API is down", null, true),
      )
      .mockResolvedValue(SYNTHETIC_EVALUATION_RUNS);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("evl-20261015T000000000000Z-eeee4444");
    expect(evaluationRuns).toHaveBeenCalledTimes(2);
  });
});

describe("evaluation report detail", () => {
  it("renders cohorts, verdicts, guardrails, and the no-causation note", async () => {
    evaluationRuns.mockResolvedValue(SYNTHETIC_EVALUATION_RUNS);
    evaluationRun.mockResolvedValue(SYNTHETIC_EVALUATION_RUN_RESPONSE);
    const user = userEvent.setup();
    renderPage();
    const report = await openReport(user);

    expect(evaluationRun).toHaveBeenCalledWith(
      "job-invoicing",
      "evl-20261015T000000000000Z-eeee4444",
    );

    // Cohorts and aggregates.
    expect(
      report.getByRole("heading", { name: "baseline" }),
    ).toBeInTheDocument();
    expect(
      report.getByRole("heading", { name: "after" }),
    ).toBeInTheDocument();
    expect(report.getByText("Declared baseline")).toBeInTheDocument();
    // Dispositions render per cohort: paired rows, pending, invalid,
    // ambiguous — and the excluded input is visible with its reason.
    expect(report.getAllByText("paired").length).toBeGreaterThan(0);
    expect(report.getAllByText(/^pending/).length).toBeGreaterThan(0);
    expect(report.getAllByText(/^invalid/).length).toBeGreaterThan(0);
    expect(report.getAllByText(/^ambiguous/).length).toBeGreaterThan(0);
    expect(report.getByText(/^Excluded/)).toBeInTheDocument();
    expect(report.getByText("107")).toBeInTheDocument();
    expect(report.getByText("future-dated start event")).toBeInTheDocument();
    expect(report.getByText(/[Uu]npaired ends/)).toBeInTheDocument();

    // Target and overall verdicts — not_met is failure, not success.
    expect(report.getByText("missed").className).toContain("status-bad");
    const overall = report.getByText("not_met");
    expect(overall.className).toContain("status-bad");

    // Guardrails: passed is green, unavailable is visibly distinct.
    const passed = report.getByText("passed");
    const unavailable = report.getByText("unavailable");
    expect(passed.className).toContain("status-ok");
    expect(unavailable.className).toContain("status-warn");
    expect(unavailable.className).not.toContain("status-ok");
    expect(
      report.getByText(/no registered semantics/),
    ).toBeInTheDocument();

    // Change direction renders as the measured improvement.
    expect(report.getByText("improvement").className).toContain("status-ok");

    // The no-causation note is displayed verbatim.
    expect(
      report.getByText(/does not establish that any intervention caused it/),
    ).toBeInTheDocument();
  });

  it("an unverified report is never styled as success", async () => {
    evaluationRuns.mockResolvedValue(SYNTHETIC_EVALUATION_RUNS);
    evaluationRun.mockResolvedValue({
      api_version: "v1",
      workspace: "/abs/example/job-invoicing",
      ok: true,
      run: SYNTHETIC_EVALUATION_RUN_UNVERIFIED,
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("evl-20261016T000000000000Z-ffff5555");
    await user.click(
      screen.getByRole("button", {
        name: /evl-20261016T000000000000Z-ffff5555/,
      }),
    );
    await screen.findByText("Cohorts");
    const report = within(
      document.querySelector(".evaluation-report") as HTMLElement,
    );

    // Assert on badge elements only — bare "unavailable" also appears for
    // a null aggregate, and that text is not a verdict.
    const badges = Array.from(
      document.querySelectorAll(".evaluation-report .status-badge"),
    );
    const unverified = badges.filter((b) => b.textContent === "unverified");
    expect(unverified.length).toBeGreaterThan(0);
    for (const verdict of unverified) {
      expect(verdict.className).toContain("status-unknown");
      expect(verdict.className).not.toContain("status-ok");
    }
    const unavailable = badges.filter((b) => b.textContent === "unavailable");
    expect(unavailable.length).toBeGreaterThan(0);
    for (const verdict of unavailable) {
      expect(verdict.className).toContain("status-warn");
      expect(verdict.className).not.toContain("status-ok");
    }
    expect(
      report.getByRole("heading", { name: /Verdict unverified/ }),
    ).toBeInTheDocument();
  });

  it("renders an unknown-run lookup as diagnostics, not an error page", async () => {
    evaluationRuns.mockResolvedValue(SYNTHETIC_EVALUATION_RUNS);
    evaluationRun.mockResolvedValue(SYNTHETIC_EVALUATION_UNKNOWN_RUN);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("evl-20261015T000000000000Z-eeee4444");
    await user.click(
      screen.getByRole("button", {
        name: /evl-20261015T000000000000Z-eeee4444/,
      }),
    );

    await screen.findByText("EVALUATION.UNKNOWN_RUN");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a retryable error state when the detail fetch fails", async () => {
    evaluationRuns.mockResolvedValue(SYNTHETIC_EVALUATION_RUNS);
    evaluationRun
      .mockRejectedValueOnce(
        new ApiError("WEB.API_UNREACHABLE", "API is down", null, true),
      )
      .mockResolvedValue(SYNTHETIC_EVALUATION_RUN_RESPONSE);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("evl-20261015T000000000000Z-eeee4444");
    await user.click(
      screen.getByRole("button", {
        name: /evl-20261015T000000000000Z-eeee4444/,
      }),
    );
    await screen.findByRole("alert");

    // The Retry inside the report error state refetches the run.
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Cohorts");
    expect(evaluationRun).toHaveBeenCalledTimes(2);
  });
});
