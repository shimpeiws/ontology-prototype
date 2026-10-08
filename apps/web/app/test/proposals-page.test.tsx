/**
 * Proposals page tests (#63) — synthetic fixture mode (fixtures.ts).
 * `api.proposals` and `api.decide` are mocked; the page renders inside a
 * seeded WorkspaceProvider. The card shows the exact persisted payload
 * and digest; the AI rationale is labeled a hypothesis; decisions post
 * through `api.decide` and refresh the list; executions are status-only.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../lib/api";
import type { DecisionResponse, ProposalsResponse } from "../lib/types";
import ProposalsPage from "../routes/workspace.proposals";
import { WorkspaceProvider } from "../workspace/context";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import type { WorkspaceState } from "../workspace/store";
import {
  SYNTHETIC_DECIDE_DOMAIN_FAILURE,
  SYNTHETIC_DECIDE_RECORDED,
  SYNTHETIC_OPEN_RESPONSE,
  SYNTHETIC_PROPOSALS_AFTER_APPROVE,
  SYNTHETIC_PROPOSALS_EMPTY,
  SYNTHETIC_PROPOSALS_RESPONSE,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: {
      ...mod.api,
      proposals: vi.fn(),
      decide: vi.fn(),
    },
  };
});

const proposals = vi.mocked(api.proposals);
const decide = vi.mocked(api.decide);

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
      <ProposalsPage />
    </WorkspaceProvider>,
  );
}

/** The card region for one proposal id. */
function cardFor(proposalId: string): HTMLElement {
  const heading = screen.getByRole("heading", { name: (content) =>
    content.includes(proposalId) });
  return heading.closest("article") as HTMLElement;
}

afterEach(() => {
  proposals.mockReset();
  decide.mockReset();
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
    expect(view.getByText("Job/101")).toBeInTheDocument();
    expect(view.getByText("Person/taro")).toBeInTheDocument();

    // AI rationale is labeled a hypothesis — never presented as fact.
    expect(view.getByText(/hypothesis/)).toBeInTheDocument();
    expect(
      view.getByText("Job 101 completed 4 days ago without an invoice."),
    ).toBeInTheDocument();
    expect(view.getByText("grounding evidence")).toBeInTheDocument();

    // Undecided proposals show an empty decision history.
    expect(
      view.getByText("No human decisions recorded."),
    ).toBeInTheDocument();
  });

  it("renders execution history as status only — no execute affordance", async () => {
    proposals.mockResolvedValue(SYNTHETIC_PROPOSALS_RESPONSE);
    renderPage();
    const card = await screen
      .findByText("dgx-20261004T090000000000Z-bbbb1111-p0")
      .then((el) => el.closest("article") as HTMLElement);
    const view = within(card);

    // The recorded attempt is visible with its status…
    expect(
      view.getByText("exe-20261004T101000000000Z-aaaa6666"),
    ).toBeInTheDocument();
    expect(view.getByText("succeeded")).toBeInTheDocument();
    // …but the only controls are the decision pair — nothing can
    // trigger a run or a write-back from here.
    expect(
      view.queryByRole("button", { name: /execute|run|trigger/i }),
    ).toBeNull();
    expect(
      view.getByRole("button", { name: "Approve" }),
    ).toBeInTheDocument();
    expect(
      view.getByRole("button", { name: "Reject" }),
    ).toBeInTheDocument();
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
    await waitFor(() =>
      expect(
        within(cardFor("dgx-20261005T120600000000Z-cccc2222-p0"))
          .getByText(/fde-hana/),
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
