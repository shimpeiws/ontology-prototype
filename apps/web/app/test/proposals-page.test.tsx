/**
 * Proposals page tests (#63, #84) — synthetic fixture mode
 * (fixtures.ts). `api.proposals`, `api.decide`, and `api.execute` are
 * mocked; the page renders inside a seeded WorkspaceProvider. The card
 * shows the exact persisted payload and digest; the AI rationale is
 * labeled a hypothesis; decisions post through `api.decide`; an
 * approved proposal executes through an armed confirm (#84 / ADR 0004)
 * that shows the receipt or the refusal diagnostics in place.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../lib/api";
import type {
  DecisionResponse,
  DiagnosisRunShowResponse,
  ProposalsResponse,
} from "../lib/types";
import { ProposalReviewCard } from "../components/ProposalReview";
import ProposalsPage from "../routes/workspace.proposals";
import { WorkspaceProvider } from "../workspace/context";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import type { WorkspaceState } from "../workspace/store";
import {
  SYNTHETIC_ANCHOR_GENERIC,
  SYNTHETIC_DECIDE_DOMAIN_FAILURE,
  SYNTHETIC_DECIDE_RECORDED,
  SYNTHETIC_DIAGNOSIS_RUN_RESPONSE,
  SYNTHETIC_DIAGNOSIS_UNKNOWN_RUN,
  SYNTHETIC_OPEN_RESPONSE,
  SYNTHETIC_PROPOSALS_AFTER_APPROVE,
  SYNTHETIC_PROPOSALS_EMPTY,
  SYNTHETIC_PROPOSALS_RESPONSE,
  SYNTHETIC_PROPOSAL_ANCHOR_RESPONSE,
  SYNTHETIC_PROPOSAL_OPEN,
  SYNTHETIC_TRACE_UNKNOWN_ID,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: {
      ...mod.api,
      proposals: vi.fn(),
      decide: vi.fn(),
      execute: vi.fn(),
      diagnosisRun: vi.fn(),
      traceRun: vi.fn(),
    },
  };
});

const proposals = vi.mocked(api.proposals);
const decide = vi.mocked(api.decide);
const execute = vi.mocked(api.execute);
const diagnosisRun = vi.mocked(api.diagnosisRun);
const traceRun = vi.mocked(api.traceRun);

/** The provenance reads (#96/#97): the open proposal's run resolves
 *  with its recorded deliverables, other ids report unknown; traceRun
 *  hands back the proposal's recorded links. */
function mockProvenance() {
  diagnosisRun.mockImplementation((_path, runId) =>
    Promise.resolve(
      runId === "dgx-20261005T120600000000Z-cccc2222"
        ? SYNTHETIC_DIAGNOSIS_RUN_RESPONSE
        : SYNTHETIC_DIAGNOSIS_UNKNOWN_RUN,
    ),
  );
  traceRun.mockImplementation((_path, runId) =>
    Promise.resolve(
      runId === "dgx-20261005T120600000000Z-cccc2222-p0"
        ? SYNTHETIC_PROPOSAL_ANCHOR_RESPONSE
        : SYNTHETIC_ANCHOR_GENERIC,
    ),
  );
}

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
        <ProposalsPage />
      </WorkspaceProvider>
    </MemoryRouter>,
  );
}

/** The card region for one proposal id. */
async function cardFor(proposalId: string): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { name: (content) =>
    content.includes(proposalId) });
  return heading.closest("article") as HTMLElement;
}

beforeEach(() => {
  mockProvenance();
});

afterEach(() => {
  proposals.mockReset();
  decide.mockReset();
  execute.mockReset();
  diagnosisRun.mockReset();
  traceRun.mockReset();
});

describe("proposals page", () => {
  it("renders the exact payload, digest, evidence, and labeled AI rationale", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    renderPage();

    const card = await within(await screen.findByLabelText("proposals"))
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);
    const view = within(card);

    // The exact persisted payload — action, params, digest.
    expect(view.getByText("SendReminder")).toBeInTheDocument();
    expect(view.getByText("sha256:9f4c2d1e…payload")).toBeInTheDocument();
    expect(view.getByText("taro")).toBeInTheDocument();
    // The subject shows both as the canonical target and as the
    // candidate reference inside the evidence section.
    expect(view.getAllByText("Job/101").length).toBeGreaterThan(0);
    expect(view.getByText("Person/taro")).toBeInTheDocument();

    // AI rationale is labeled a hypothesis — never presented as fact.
    expect(
      view.getAllByText(/hypothesis/).length,
    ).toBeGreaterThan(0);
    expect(
      view.getByText("Job 101 completed 4 days ago without an invoice."),
    ).toBeInTheDocument();
    expect(
      view.getByRole("heading", { name: /Grounding evidence/ }),
    ).toBeInTheDocument();

    // Undecided proposals show an empty decision history.
    expect(
      view.getByText("No human decisions recorded."),
    ).toBeInTheDocument();
  });

  it("shows the proposing run's provider, model, and recorded output", async () => {
    // #96: the card reads the persisted diagnosis run back — provider/
    // model plus the run's deliverables survive a page reload.
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    renderPage();

    const card = await cardFor("dgx-20261005T120600000000Z-cccc2222-p0");
    const view = within(card);

    await waitFor(() =>
      expect(diagnosisRun).toHaveBeenCalledWith(
        "job-invoicing",
        "dgx-20261005T120600000000Z-cccc2222",
        expect.any(AbortSignal),
      ),
    );
    const section = await view.findByLabelText("proposing diagnosis run");

    // Provider/model with the stub marked as a test double, not a live
    // model.
    expect(
      within(section).getByText(/stub\/stub-offline/),
    ).toBeInTheDocument();
    expect(
      within(section).getByText(/deterministic test double/),
    ).toBeInTheDocument();

    // The run's recorded model output — labeled hypothesis, not fact.
    expect(
      within(section).getByText(/Hypotheses the provider recorded/),
    ).toBeInTheDocument();
    expect(
      within(section).getByText(
        /deterministic response listing the 1 candidate/,
      ),
    ).toBeInTheDocument();
    expect(
      within(section).getByText(/awaiting a manual invoicing step/),
    ).toBeInTheDocument();
    expect(
      within(section).getByText("Was a manual invoice held for review?"),
    ).toBeInTheDocument();
  });

  it("marks a live provider without the stub test-double flag", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    diagnosisRun.mockResolvedValue({
      ...SYNTHETIC_DIAGNOSIS_RUN_RESPONSE,
      run: {
        ...SYNTHETIC_DIAGNOSIS_RUN_RESPONSE.run!,
        provider: "openrouter",
        model: "openai/gpt-4o-mini",
      },
    });
    renderPage();

    const card = await cardFor("dgx-20261005T120600000000Z-cccc2222-p0");
    const section = await within(card).findByLabelText(
      "proposing diagnosis run",
    );
    expect(
      await within(section).findByText(/openrouter\/openai\/gpt-4o-mini/),
    ).toBeInTheDocument();
    expect(
      within(section).queryByText(/test double/),
    ).not.toBeInTheDocument();
  });

  it("renders evidence as labeled, linked references — unresolved ids marked", async () => {
    // #97: fact keys, observation and ingest ids become trace links;
    // an id that resolves to nothing stays visible, marked unresolved.
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    renderPage();

    const card = await cardFor("dgx-20261005T120600000000Z-cccc2222-p0");
    const view = within(card);
    const section = await view.findByLabelText("grounding evidence");

    // The recorded references render under their labels…
    expect(within(section).getByText("Fact")).toBeInTheDocument();
    expect(
      within(section).getByText("Entity observation"),
    ).toBeInTheDocument();
    expect(within(section).getByText("Observations")).toBeInTheDocument();
    expect(within(section).getByText("Ingest runs")).toBeInTheDocument();
    expect(
      within(section).getByText("Entity state observed at"),
    ).toBeInTheDocument();

    // …each linked into the Trace lookup…
    expect(
      within(section).getByRole("link", { name: "fact-s1" }),
    ).toHaveAttribute("href", "/workspace/trace?artifact=fact-s1");
    expect(
      within(section).getByRole("link", {
        name: "ing-20261005T120000000000Z-aaaa0000",
      }),
    ).toHaveAttribute(
      "href",
      "/workspace/trace?artifact=ing-20261005T120000000000Z-aaaa0000",
    );
    expect(
      within(section).getByRole("link", { name: "Job/101" }),
    ).toHaveAttribute("href", "/workspace/trace?subject=Job%2F101");

    // …and the id resolving to no recorded artifact is still shown,
    // marked unresolved.
    const missing = within(section).getByRole("link", {
      name: /obs-missing/,
    });
    expect(missing).toHaveAttribute(
      "href",
      "/workspace/trace?artifact=obs-missing",
    );
    expect(
      within(missing).getByText("unresolved"),
    ).toBeInTheDocument();
  });

  it("marks evidence unverified — not resolvable — when the anchor is unreadable", async () => {
    // FIND-001: an anchor the trace cannot resolve means the links are
    // unknown, not empty — the ids must not render as plain resolvable
    // references.
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    traceRun.mockResolvedValue(SYNTHETIC_TRACE_UNKNOWN_ID);
    renderPage();

    const card = await cardFor("dgx-20261005T120600000000Z-cccc2222-p0");
    const section = await within(card).findByLabelText(
      "grounding evidence",
    );
    expect(
      within(section).getByText(/could not be verified/),
    ).toBeInTheDocument();
    // Every reference carries the unverified badge — none reads as a
    // confirmed resolvable link, and none claims "unresolved" either.
    expect(
      within(section).getAllByText("unverified").length,
    ).toBeGreaterThan(0);
    expect(
      within(section).queryByText("unresolved"),
    ).not.toBeInTheDocument();
  });

  it("keeps the run provenance when the trace anchor read fails", async () => {
    // FIND-002: the two read-backs settle independently — a failed
    // anchor lookup must not discard the recorded run.
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    traceRun.mockRejectedValue(
      new ApiError("WEB.API_UNREACHABLE", "API is down", null, true),
    );
    renderPage();

    const card = await cardFor("dgx-20261005T120600000000Z-cccc2222-p0");
    const section = await within(card).findByLabelText(
      "proposing diagnosis run",
    );
    expect(
      await within(section).findByText(/stub\/stub-offline/),
    ).toBeInTheDocument();
    expect(
      within(section).getByText(/deterministic response listing the 1 candidate/),
    ).toBeInTheDocument();
    expect(
      within(card).getByLabelText("grounding evidence"),
    ).toHaveTextContent(/could not be verified/);
  });

  it("shows the recorded run without waiting for the trace anchor", async () => {
    // Devin Review: the two read-backs settle into separate state — a
    // slow trace lookup must not hold the already-settled run record
    // hostage, and pending references get no verdict badge yet.
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    let resolveAnchor!: (r: typeof SYNTHETIC_PROPOSAL_ANCHOR_RESPONSE) => void;
    traceRun.mockImplementation((_p, runId) =>
      runId === "dgx-20261005T120600000000Z-cccc2222-p0"
        ? new Promise<typeof SYNTHETIC_PROPOSAL_ANCHOR_RESPONSE>((r) => {
            resolveAnchor = r;
          })
        : Promise.resolve(SYNTHETIC_ANCHOR_GENERIC),
    );
    renderPage();

    const card = await cardFor("dgx-20261005T120600000000Z-cccc2222-p0");
    const section = await within(card).findByLabelText(
      "proposing diagnosis run",
    );
    expect(
      await within(section).findByText(/stub\/stub-offline/),
    ).toBeInTheDocument();

    const evidence = within(card).getByLabelText("grounding evidence");
    expect(
      within(evidence).getByText(/Verifying the recorded references/),
    ).toBeInTheDocument();
    expect(
      within(evidence).queryByText("unverified"),
    ).not.toBeInTheDocument();
    expect(
      within(evidence).queryByText("unresolved"),
    ).not.toBeInTheDocument();

    await act(async () => {
      resolveAnchor(SYNTHETIC_PROPOSAL_ANCHOR_RESPONSE);
      await Promise.resolve();
    });
    await within(evidence).findByText("unresolved");
  });

  it("a superseded provenance response never renders", async () => {
    // INV-003: the card's path changes (workspace switch) while the
    // first read is in flight — the stale response is discarded.
    let resolveStale!: (r: DiagnosisRunShowResponse) => void;
    diagnosisRun.mockImplementationOnce(
      () =>
        new Promise<DiagnosisRunShowResponse>((r) => {
          resolveStale = r;
        }),
    );
    diagnosisRun.mockResolvedValue(SYNTHETIC_DIAGNOSIS_UNKNOWN_RUN);
    const { rerender } = render(
      <MemoryRouter>
        <ProposalReviewCard
          path="ws-a"
          proposal={SYNTHETIC_PROPOSAL_OPEN}
          reviewer=""
          onDecided={() => {}}
        />
      </MemoryRouter>,
    );
    rerender(
      <MemoryRouter>
        <ProposalReviewCard
          path="ws-b"
          proposal={SYNTHETIC_PROPOSAL_OPEN}
          reviewer=""
          onDecided={() => {}}
        />
      </MemoryRouter>,
    );
    // The stale ws-a response resolves late — it must not appear.
    resolveStale(SYNTHETIC_DIAGNOSIS_RUN_RESPONSE);
    const section = await screen.findByLabelText("proposing diagnosis run");
    await within(section).findByText(/no longer readable/);
    expect(
      within(section).queryByText(/stub\/stub-offline/),
    ).not.toBeInTheDocument();
  });

  it("keeps the payload reason param and the AI rationale distinct", async () => {
    // #96: the action's `reason` param is payload content; the AI
    // rationale is model output — they render under different labels.
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    renderPage();

    const card = await cardFor("dgx-20261005T120600000000Z-cccc2222-p0");
    const view = within(card);

    const payload = view.getByLabelText("exact payload");
    expect(payload).toHaveTextContent(
      "Job exceeded the two-day threshold.",
    );
    expect(payload).toHaveTextContent(
      /payload argument, not the model's rationale/,
    );
    expect(
      view.getByLabelText("AI rationale"),
    ).toHaveTextContent(
      "Job 101 completed 4 days ago without an invoice.",
    );
  });

  it("shows the armed execute affordance on approved proposals only", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    renderPage();

    // The approved+executed card offers the trigger (replays are
    // idempotent)…
    const executed = await screen
      .findByText("dgx-20261004T090000000000Z-bbbb1111-p0")
      .then((el) => el.closest("article") as HTMLElement);
    expect(
      within(executed).getByRole("button", { name: "Execute proposal" }),
    ).toBeEnabled();
    expect(
      within(executed).getByText("exe-20261004T101000000000Z-aaaa6666"),
    ).toBeInTheDocument();

    // …the undecided card's trigger stays inert — execution requires
    // an approved decision.
    const open = await screen
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);
    const view = within(open);
    expect(
      view.getByRole("button", { name: "Execute proposal" }),
    ).toBeDisabled();
    expect(view.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("executes an approved proposal through an armed confirm", async () => {
    proposals
      .mockResolvedValueOnce(SYNTHETIC_PROPOSALS_AFTER_APPROVE)
      .mockResolvedValueOnce(SYNTHETIC_PROPOSALS_AFTER_APPROVE);
    execute.mockResolvedValue({
      api_version: "v1",
      workspace: "job-invoicing",
      ok: true,
      execution_id: "exe-20261005T150000000000Z-hhhh9999",
      idempotency_key: "idem-x",
      proposal_id: "dgx-20261005T120600000000Z-cccc2222-p0",
      adapter: "csv",
      status: "succeeded",
      attempt: 1,
      attempts: 1,
      replayed: false,
      receipt: { path: ".ontology/reminders.csv", row: 2 },
      error: null,
      recorded: true,
      diagnostics: [],
    });
    const user = userEvent.setup();
    renderPage();

    const card = await screen
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);
    const view = within(card);

    // First click arms — nothing fires yet.
    await user.click(
      view.getByRole("button", { name: "Execute proposal" }),
    );
    expect(execute).not.toHaveBeenCalled();
    await user.click(
      view.getByRole("button", { name: "Confirm write-back" }),
    );

    await waitFor(() =>
      expect(execute).toHaveBeenCalledWith(
        "job-invoicing",
        "dgx-20261005T120600000000Z-cccc2222-p0",
      ),
    );
    // The receipt renders in place — run identity, attempt, receipt.
    await view.findByText("exe-20261005T150000000000Z-hhhh9999");
    expect(view.getByText("succeeded")).toBeInTheDocument();
    expect(view.getByText("receipt")).toBeInTheDocument();
  });

  it("renders the refusal diagnostics when the gate refuses", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_AFTER_APPROVE);
    execute.mockResolvedValue({
      api_version: "v1",
      workspace: "job-invoicing",
      ok: false,
      diagnostics: [
        {
          severity: "error",
          code: "DECISION.NOT_APPROVED",
          message: "latest decision is not an approval",
          file: null,
          path: null,
          line: null,
          hint: null,
        },
      ],
    });
    const user = userEvent.setup();
    renderPage();

    const card = await screen
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);
    const view = within(card);
    await user.click(
      view.getByRole("button", { name: "Execute proposal" }),
    );
    await user.click(
      view.getByRole("button", { name: "Confirm write-back" }),
    );
    await view.findByText("DECISION.NOT_APPROVED");
  });

  it("marks a replayed execution as already executed", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    execute.mockResolvedValue({
      api_version: "v1",
      workspace: "job-invoicing",
      ok: true,
      execution_id: "exe-20261004T101000000000Z-aaaa6666",
      idempotency_key: "idem-20261004T101000000000Z-bbbb7777",
      proposal_id: "dgx-20261004T090000000000Z-bbbb1111-p0",
      adapter: "csv",
      status: "succeeded",
      attempt: 1,
      attempts: 1,
      replayed: true,
      receipt: { path: ".ontology/reminders.csv", row: 2 },
      error: null,
      recorded: true,
      diagnostics: [],
    });
    const user = userEvent.setup();
    renderPage();

    const card = await screen
      .findByText("dgx-20261004T090000000000Z-bbbb1111-p0")
      .then((el) => el.closest("article") as HTMLElement);
    const view = within(card);
    await user.click(
      view.getByRole("button", { name: "Execute proposal" }),
    );
    await user.click(
      view.getByRole("button", { name: "Confirm write-back" }),
    );
    await view.findByText(/already executed/);
  });

  it("records an approval through the API and refreshes the card", async () => {
    proposals
      .mockResolvedValueOnce(SYNTHETIC_PROPOSALS_RESPONSE)
      .mockResolvedValueOnce(SYNTHETIC_PROPOSALS_AFTER_APPROVE);
    decide.mockResolvedValue(SYNTHETIC_DECIDE_RECORDED);
    const user = userEvent.setup();
    renderPage();

    const card = await screen
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);

    await user.type(
      screen.getByLabelText("Reviewer identity"),
      "fde-hana",
    );
    await user.type(
      within(card).getByLabelText(/Reason/),
      "verified against the ledger",
    );
    await user.click(within(card).getByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(decide).toHaveBeenCalledWith(
        "job-invoicing",
        "dgx-20261005T120600000000Z-cccc2222-p0",
        "approved",
        "fde-hana",
        "verified against the ledger",
      ),
    );
    // The recorded confirmation shows, then the refreshed list renders
    // the new status and the appended decision — the id appears twice
    // (confirmation + decision history).
    await screen.findAllByText("dec-20261005T140000000000Z-gggg8888");
    await waitFor(() => expect(proposals).toHaveBeenCalledTimes(2));
    await waitFor(async () =>
      expect(
        within(
          await cardFor("dgx-20261005T120600000000Z-cccc2222-p0"),
        ).getByText(/fde-hana/),
      ).toBeInTheDocument(),
    );
  });

  it("records a rejection the same way", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    decide.mockResolvedValue({
      ...SYNTHETIC_DECIDE_RECORDED,
      decision: "rejected",
    });
    const user = userEvent.setup();
    renderPage();

    const card = await screen
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);

    await user.type(
      screen.getByLabelText("Reviewer identity"),
      "fde-hana",
    );
    await user.click(within(card).getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(decide).toHaveBeenCalledWith(
        "job-invoicing",
        "dgx-20261005T120600000000Z-cccc2222-p0",
        "rejected",
        "fde-hana",
        null,
      ),
    );
  });

  it("disables the decision buttons while the reviewer identity is blank", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    renderPage();
    const card = await screen
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);
    const view = within(card);

    expect(view.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(view.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(decide).not.toHaveBeenCalled();
  });

  it("renders domain failures as diagnostics, not transport errors", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    decide.mockResolvedValue(SYNTHETIC_DECIDE_DOMAIN_FAILURE);
    const user = userEvent.setup();
    renderPage();

    const card = await screen
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);

    await user.type(
      screen.getByLabelText("Reviewer identity"),
      "fde-hana",
    );
    await user.click(within(card).getByRole("button", { name: "Approve" }));

    await within(card).findByText("DECISION.UNKNOWN_PROPOSAL");
    // A domain failure must not refresh into a misleading state.
    expect(proposals).toHaveBeenCalledTimes(1);
  });

  it("renders lock contention as a retryable transport error", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    decide
      .mockRejectedValueOnce(
        new ApiError(
          "STORAGE.WRITE_BUSY",
          "cannot record decision: database is locked",
          503,
          true,
        ),
      )
      .mockResolvedValue(SYNTHETIC_DECIDE_RECORDED);
    const user = userEvent.setup();
    renderPage();

    const card = await screen
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);
    const view = within(card);

    await user.type(
      screen.getByLabelText("Reviewer identity"),
      "fde-hana",
    );
    await user.click(view.getByRole("button", { name: "Approve" }));

    const alert = await within(card).findByRole("alert");
    expect(alert).toHaveTextContent("STORAGE.WRITE_BUSY");

    // Retry resubmits the same decision — the error must not silently
    // flip the recorded outcome.
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(decide).toHaveBeenLastCalledWith(
        "job-invoicing",
        "dgx-20261005T120600000000Z-cccc2222-p0",
        "approved",
        "fde-hana",
        null,
      ),
    );
  });

  it("shows the empty state when nothing is persisted", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_EMPTY);
    renderPage();
    await screen.findByText(/No persisted proposals/);
  });

  it("refetches the queue on Refresh and renders newly persisted proposals", async () => {
    // A proposal persisted by `ontology diagnose` after page load only
    // shows up once the reviewer asks for fresh state.
    proposals
      .mockResolvedValueOnce(SYNTHETIC_PROPOSALS_EMPTY)
      .mockResolvedValueOnce(SYNTHETIC_PROPOSALS_RESPONSE);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/No persisted proposals/);
    await user.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(proposals).toHaveBeenCalledTimes(2));
    await screen.findByText("dgx-20261005T120600000000Z-cccc2222-p0");
  });

  it("keeps the rendered queue on screen while a refresh is in flight", async () => {
    let resolveSecond!: (r: ProposalsResponse) => void;
    proposals
      .mockResolvedValueOnce(SYNTHETIC_PROPOSALS_RESPONSE)
      .mockReturnValueOnce(
        new Promise<ProposalsResponse>((r) => {
          resolveSecond = r;
        }),
      );
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("dgx-20261005T120600000000Z-cccc2222-p0");
    await user.click(screen.getByRole("button", { name: "Refresh" }));

    // A same-page refetch never flashes the spinner over the queue the
    // reviewer is looking at; the button reports the in-flight refresh.
    expect(
      screen.getByText("dgx-20261005T120600000000Z-cccc2222-p0"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Refreshing…" }),
    ).toBeDisabled();

    resolveSecond(SYNTHETIC_PROPOSALS_AFTER_APPROVE);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Refresh" }),
      ).toBeEnabled(),
    );
  });

  it("locks both buttons while a decision is in flight", async () => {
    // Single-flight: a reviewer cannot fire a second decision before the
    // first settles — and a late response can never arrive after a newer
    // submission because none can start.
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    let resolveFirst!: (r: DecisionResponse) => void;
    decide.mockReturnValueOnce(
      new Promise<DecisionResponse>((r) => {
        resolveFirst = r;
      }),
    );
    const user = userEvent.setup();
    renderPage();

    const card = await screen
      .findByText("dgx-20261005T120600000000Z-cccc2222-p0")
      .then((el) => el.closest("article") as HTMLElement);
    const view = within(card);
    await user.type(
      screen.getByLabelText("Reviewer identity"),
      "fde-hana",
    );

    await user.click(view.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    expect(
      view.getByRole("button", { name: "Recording…" }),
    ).toBeDisabled();
    expect(view.getByRole("button", { name: "Reject" })).toBeDisabled();

    resolveFirst(SYNTHETIC_DECIDE_RECORDED);
    await screen.findAllByText("dec-20261005T140000000000Z-gggg8888");
    await waitFor(() =>
      expect(
        view.getByRole("button", { name: "Reject" }),
      ).toBeEnabled(),
    );
  });
});
