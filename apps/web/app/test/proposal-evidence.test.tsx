/**
 * Proposal grounding-evidence tests (#97) — synthetic fixture mode
 * (fixtures.ts). `api.traceRun` is mocked with the proposal's own trace
 * anchor so unresolved marking follows the core's link verdicts; the
 * card renders inside a MemoryRouter because the references are real
 * links into the Trace screen.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../lib/api";
import { ProposalReviewCard } from "../components/ProposalReview";
import type { ProposalView } from "../lib/types";
import {
  SYNTHETIC_PROPOSAL_ANCHOR,
  SYNTHETIC_PROPOSAL_ANCHOR_RESOLVED,
  SYNTHETIC_PROPOSAL_OPEN,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: {
      ...mod.api,
      traceRun: vi.fn(),
    },
  };
});

const traceRun = vi.mocked(api.traceRun);

function renderCard(proposal: ProposalView) {
  render(
    <MemoryRouter>
      <ProposalReviewCard
        path="job-invoicing"
        proposal={proposal}
        reviewer="fde-hana"
        onDecided={() => {}}
      />
    </MemoryRouter>,
  );
  return screen.getByLabelText("grounding evidence");
}

afterEach(() => {
  traceRun.mockReset();
});

describe("proposal grounding evidence", () => {
  it("renders evidence ids as trace links and keeps unknown context visible", async () => {
    traceRun.mockResolvedValue(SYNTHETIC_PROPOSAL_ANCHOR_RESOLVED);
    const section = renderCard(SYNTHETIC_PROPOSAL_OPEN);
    const view = within(section as HTMLElement);

    // Each evidence id deep-links to the Trace screen's artifact lookup.
    expect(view.getByRole("link", { name: "fact-s1" })).toHaveAttribute(
      "href",
      "/workspace/trace?run=fact-s1",
    );
    expect(view.getByRole("link", { name: "obs-0001" })).toHaveAttribute(
      "href",
      "/workspace/trace?run=obs-0001",
    );
    expect(
      view.getByRole("link", {
        name: "ing-20261005T120000000000Z-aaaa0000",
      }),
    ).toHaveAttribute(
      "href",
      "/workspace/trace?run=ing-20261005T120000000000Z-aaaa0000",
    );
    // obs-0002 is cited twice — an observation and the candidate's
    // entity observation — and both render as links.
    for (const link of view.getAllByRole("link", { name: "obs-0002" })) {
      expect(link).toHaveAttribute("href", "/workspace/trace?run=obs-0002");
    }
    // The candidate/as-of context is labeled, not a raw key in JSON.
    expect(view.getByText("Candidate state as of")).toBeInTheDocument();
    expect(view.getByText("2026-10-05T12:00:00Z")).toBeInTheDocument();
    // Unrecognized evidence keys still render as context — nothing the
    // record carries is dropped.
    expect(view.getByText("elapsed_days")).toBeInTheDocument();
    expect(view.getByText("4")).toBeInTheDocument();
    expect(view.getByText("threshold_days")).toBeInTheDocument();

    // The card's other references deep-link too — the candidate subject
    // and the diagnosis run that produced the proposal.
    const card = section.closest("article") as HTMLElement;
    expect(
      within(card).getByRole("link", { name: "Job/101" }),
    ).toHaveAttribute("href", "/workspace/trace?subject=Job%2F101");
    expect(
      within(card).getByRole("link", { name: "Person/taro" }),
    ).toHaveAttribute("href", "/workspace/trace?subject=Person%2Ftaro");
    expect(
      within(card).getByRole("link", {
        name: "dgx-20261005T120600000000Z-cccc2222",
      }),
    ).toHaveAttribute(
      "href",
      "/workspace/trace?run=dgx-20261005T120600000000Z-cccc2222",
    );

    // Resolution came from the proposal's own anchor — not a guess.
    await waitFor(() =>
      expect(traceRun).toHaveBeenCalledWith(
        "job-invoicing",
        "dgx-20261005T120600000000Z-cccc2222-p0",
        expect.any(AbortSignal),
      ),
    );
  });

  it("marks evidence ids the trace graph cannot resolve", async () => {
    traceRun.mockResolvedValue(SYNTHETIC_PROPOSAL_ANCHOR);
    const section = renderCard(SYNTHETIC_PROPOSAL_OPEN);
    const view = within(section as HTMLElement);

    // obs-0002 is listed in the anchor's unresolved links — it stays
    // shown, marked, and no longer pretends to be navigable. It is
    // cited twice (observations + candidate entity observation).
    const badges = await view.findAllByText("unresolved");
    expect(badges.length).toBe(2);
    expect(view.queryByRole("link", { name: "obs-0002" })).toBeNull();
    expect(view.getAllByText("obs-0002").length).toBe(2);

    // Resolved ids keep their links.
    expect(view.getByRole("link", { name: "obs-0001" })).toHaveAttribute(
      "href",
      "/workspace/trace?run=obs-0001",
    );
  });

  it("drops a stale verdict when the workspace/proposal pair changes", async () => {
    traceRun.mockResolvedValue(SYNTHETIC_PROPOSAL_ANCHOR);
    const { rerender } = render(
      <MemoryRouter>
        <ProposalReviewCard
          path="job-invoicing"
          proposal={SYNTHETIC_PROPOSAL_OPEN}
          reviewer="fde-hana"
          onDecided={() => {}}
        />
      </MemoryRouter>,
    );
    await screen.findAllByText("unresolved");

    // Workspace switch, same proposal id: the new anchor lookup fails —
    // the previous workspace's verdict must not keep silencing links.
    traceRun.mockRejectedValue(new Error("API down"));
    rerender(
      <MemoryRouter>
        <ProposalReviewCard
          path="other-workspace"
          proposal={SYNTHETIC_PROPOSAL_OPEN}
          reviewer="fde-hana"
          onDecided={() => {}}
        />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.queryByText("unresolved")).toBeNull());
    for (const link of screen.getAllByRole("link", { name: "obs-0002" })) {
      expect(link).toHaveAttribute("href", "/workspace/trace?run=obs-0002");
    }
  });

  it("shows every recorded list member, id or not", async () => {
    traceRun.mockResolvedValue(SYNTHETIC_PROPOSAL_ANCHOR_RESOLVED);
    const proposal: ProposalView = {
      ...SYNTHETIC_PROPOSAL_OPEN,
      evidence: {
        observation_ids: ["obs-0001", 42],
        ingest_ids: [null],
      },
    };
    const section = renderCard(proposal);
    const view = within(section as HTMLElement);

    // The string member links; the recorded non-id values stay visible
    // instead of being filtered into silence.
    await view.findByRole("link", { name: "obs-0001" });
    expect(view.getByText("42")).toBeInTheDocument();
    expect(view.getByText("null")).toBeInTheDocument();
  });

  it("renders absent or mistyped evidence values as context", async () => {
    traceRun.mockResolvedValue(SYNTHETIC_PROPOSAL_ANCHOR_RESOLVED);
    const proposal: ProposalView = {
      ...SYNTHETIC_PROPOSAL_OPEN,
      evidence: {
        fact_key: "fact-s1",
        observation_ids: [],
        entity_observation_id: null,
      },
    };
    const section = renderCard(proposal);
    const view = within(section as HTMLElement);

    await view.findByRole("link", { name: "fact-s1" });
    // A recorded-but-empty list and a null id stay visible as themselves.
    expect(view.getByText("Observations")).toBeInTheDocument();
    expect(view.getByText("none")).toBeInTheDocument();
    expect(
      view.getByText("Candidate entity observation"),
    ).toBeInTheDocument();
    expect(view.getByText("null")).toBeInTheDocument();
  });

  it("shows an explicit empty state when no evidence was recorded", async () => {
    traceRun.mockResolvedValue(SYNTHETIC_PROPOSAL_ANCHOR_RESOLVED);
    const proposal: ProposalView = {
      ...SYNTHETIC_PROPOSAL_OPEN,
      evidence: {},
    };
    const section = renderCard(proposal);

    expect(
      await within(section as HTMLElement).findByText(
        "No grounding evidence was recorded.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps references navigable when the anchor lookup fails", async () => {
    traceRun.mockRejectedValue(new Error("API down"));
    const section = renderCard(SYNTHETIC_PROPOSAL_OPEN);
    const view = within(section as HTMLElement);

    await waitFor(() => expect(traceRun).toHaveBeenCalled());
    expect(view.getByRole("link", { name: "fact-s1" })).toBeInTheDocument();
    expect(view.queryByText("unresolved")).toBeNull();
  });
});
