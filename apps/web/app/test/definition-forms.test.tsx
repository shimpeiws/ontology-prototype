/**
 * Structured definition form tests (#18) — synthetic fixture mode
 * (fixtures.ts): `api.contract` is mocked; drafts live in the workspace
 * store and are asserted through a probe, never through a real API.
 */

import { useEffect } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../lib/api";
import { clearContractCache } from "../lib/contract";
import { useWorkspace, WorkspaceProvider } from "../workspace/context";
import type { OpenResponse, WorkspaceFileName } from "../lib/types";
import { DraftEditor } from "../components/DraftEditor";
import { EvaluationForm } from "../components/EvaluationForm";
import { AgentForm } from "../components/AgentForm";
import { FieldDiagnostics, TextField } from "../components/FormControls";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "../workspace/store";
import {
  SYNTHETIC_CONTRACT_RESPONSE,
  SYNTHETIC_DIAGNOSTIC,
  SYNTHETIC_FORM_OPEN_RESPONSE,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: { ...mod.api, contract: vi.fn() },
  };
});

const contractMock = vi.mocked(api.contract);

beforeEach(() => {
  clearContractCache();
  contractMock.mockResolvedValue(SYNTHETIC_CONTRACT_RESPONSE);
});

afterEach(() => {
  contractMock.mockReset();
});

function Harness({
  children,
  response = SYNTHETIC_FORM_OPEN_RESPONSE,
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

/** Reads the live draft text of one file out of the store. */
function DraftProbe({ file }: { file: WorkspaceFileName }) {
  const { state } = useWorkspace();
  return (
    <output data-testid={`draft-${file}`}>{state.drafts[file] ?? ""}</output>
  );
}

function renderForm(
  children: React.ReactNode,
  response?: OpenResponse,
  file: WorkspaceFileName = "evaluation.yaml",
) {
  return render(
    <WorkspaceProvider>
      <Harness response={response}>
        {children}
        <DraftProbe file={file} />
      </Harness>
    </WorkspaceProvider>,
  );
}

function draftText(file: WorkspaceFileName = "evaluation.yaml"): string {
  return screen.getByTestId(`draft-${file}`).textContent ?? "";
}

async function waitForContract() {
  await waitFor(() => expect(contractMock).toHaveBeenCalled());
  // Let the resolved contract land in state before asserting on options.
  await screen.findByDisplayValue("median").catch(() => undefined);
}

describe("EvaluationForm", () => {
  it("hydrates fields from the draft YAML", async () => {
    renderForm(<EvaluationForm />);
    expect(screen.getByLabelText("Name")).toHaveValue(
      "completion-to-invoice-latency",
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Aggregation")).toHaveValue("median"),
    );
    expect(screen.getByLabelText("Metric")).toHaveValue("duration");
    expect(screen.getByLabelText("Unit")).toHaveValue("days");
    expect(screen.getByLabelText("Start event")).toHaveValue("JobCompleted");
    expect(screen.getByLabelText("End event")).toHaveValue("InvoiceIssued");
    // Baseline value 9 and strict target lt 4 are editable in place.
    expect(
      screen.getByRole("textbox", { name: "Baseline value" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Baseline value")).toHaveValue(
      "9",
    );
    expect(screen.getByLabelText("Target comparator")).toHaveValue("lt");
    expect(screen.getByLabelText("Guardrail 1 name")).toHaveValue(
      "invoice-error-rate",
    );
  });

  it("vocabulary options come from the contract, not hardcoded lists", async () => {
    renderForm(<EvaluationForm />);
    await waitForContract();
    const aggregation = screen.getByLabelText("Aggregation");
    const optionTexts = Array.from(aggregation.querySelectorAll("option")).map(
      (o) => o.value,
    );
    expect(optionTexts).toEqual(
      expect.arrayContaining(["median", "mean", "min", "max", "count"]),
    );
    expect(optionTexts).not.toContain("p95");
    const metricOptions = Array.from(
      screen.getByLabelText("Metric").querySelectorAll("option"),
    ).map((o) => o.value);
    expect(metricOptions).toContain("duration");
    expect(metricOptions).not.toContain("error_rate");
  });

  it("editing a field writes YAML to the draft", async () => {
    const user = userEvent.setup();
    renderForm(<EvaluationForm />);
    await waitForContract();

    const name = screen.getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "renamed-eval");
    expect(draftText()).toContain("name: renamed-eval");
    expect(draftText()).toContain("schema_version: 1");
  });

  it("form edits preserve comments and unexposed fields", async () => {
    const user = userEvent.setup();
    renderForm(<EvaluationForm />);
    await waitForContract();

    await user.selectOptions(screen.getByLabelText("Aggregation"), "mean");
    expect(draftText()).toContain("aggregation: mean");
    // Untouched content survives the AST edit.
    expect(draftText()).toContain("# comment proving form edits preserve");
    expect(draftText()).toContain(
      "description: Baseline median completion-to-invoice latency.",
    );
    expect(draftText()).toContain("kind: evaluation");
  });

  it("a non-numeric baseline is written verbatim for core diagnostics", async () => {
    const user = userEvent.setup();
    renderForm(<EvaluationForm />);
    await waitForContract();

    const baseline = screen.getByLabelText("Baseline value");
    await user.clear(baseline);
    await user.type(baseline, "abc");
    expect(draftText()).toContain("value: abc");
  });

  it("adds and removes guardrails", async () => {
    const user = userEvent.setup();
    renderForm(<EvaluationForm />);
    await waitForContract();

    await user.click(screen.getByRole("button", { name: "Add guardrail" }));
    expect(screen.getByText("Guardrail 2")).toBeInTheDocument();
    expect(draftText()).toContain("guardrails:");

    await user.click(
      screen.getAllByRole("button", { name: "Remove guardrail" })[0],
    );
    expect(draftText()).not.toContain("invoice-error-rate");
    expect(screen.queryByText("Guardrail 2")).toBeNull();
  });

  it("after removing a guardrail, edits target the surviving item (FIND-001)", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_FORM_OPEN_RESPONSE,
      files: {
        ...SYNTHETIC_FORM_OPEN_RESPONSE.files,
        "evaluation.yaml":
          SYNTHETIC_FORM_OPEN_RESPONSE.files["evaluation.yaml"] +
          "    - name: second-guardrail\n      metric: other_metric\n      comparator: lt\n      value: 2\n",
      },
    };
    const user = userEvent.setup();
    renderForm(<EvaluationForm />, response);
    await waitForContract();
    expect(screen.getByLabelText("Guardrail 2 name")).toHaveValue(
      "second-guardrail",
    );

    await user.click(
      screen.getAllByRole("button", { name: "Remove guardrail" })[0],
    );
    expect(draftText()).not.toContain("invoice-error-rate");

    // The survivor is now "Guardrail 1"; editing it must write to it.
    const survivor = screen.getByLabelText("Guardrail 1 name");
    await user.clear(survivor);
    await user.type(survivor, "renamed-guardrail");
    expect(draftText()).toContain("name: renamed-guardrail");
    expect(draftText()).toContain("other_metric");
  });

  it("renders an item-level diagnostic inside its guardrail card (FIND-003)", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_FORM_OPEN_RESPONSE,
      diagnostics: [
        {
          severity: "error",
          code: "EVAL.BAD_GUARDRAIL",
          message: "guardrail item must be a mapping",
          file: "evaluation.yaml",
          path: "evaluation.guardrails[0]",
          line: null,
          hint: null,
        },
      ],
    };
    renderForm(<EvaluationForm />, response);
    await waitForContract();
    const card = screen.getByText("Guardrail 1").closest(".form-card");
    expect(card).toHaveTextContent("EVAL.BAD_GUARDRAIL");
  });

  it("an empty vocabulary degrades the select to free text (FIND-002)", async () => {
    contractMock.mockResolvedValue({
      ...SYNTHETIC_CONTRACT_RESPONSE,
      contract: {
        ...SYNTHETIC_CONTRACT_RESPONSE.contract,
        evaluation: {
          ...(SYNTHETIC_CONTRACT_RESPONSE.contract["evaluation"] as Record<
            string,
            unknown
          >),
          aggregations: [],
        },
      },
    } as typeof SYNTHETIC_CONTRACT_RESPONSE);
    const user = userEvent.setup();
    renderForm(<EvaluationForm />);
    await waitFor(() =>
      expect(screen.getByLabelText("Aggregation").tagName).toBe("INPUT"),
    );
    const aggregation = screen.getByLabelText("Aggregation");
    await user.clear(aggregation);
    await user.type(aggregation, "mean");
    expect(draftText()).toContain("aggregation: mean");
  });

  it("attaches field-level diagnostics to the owning field", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_FORM_OPEN_RESPONSE,
      diagnostics: [
        {
          severity: "error",
          code: "EVAL.UNKNOWN_EVENT",
          message: "unknown event type 'JobComplete'",
          file: "evaluation.yaml",
          path: "evaluation.interval.start_event",
          line: null,
          hint: "did you mean 'JobCompleted'?",
        },
      ],
    };
    renderForm(<EvaluationForm />, response);
    await waitForContract();

    const field = screen.getByLabelText("Start event").closest(".form-field");
    expect(field).toHaveTextContent("EVAL.UNKNOWN_EVENT");
    expect(field).toHaveTextContent("did you mean 'JobCompleted'?");
    expect(screen.getByLabelText("Start event")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    // A field the diagnostic does not target stays clean.
    const name = screen.getByLabelText("Name").closest(".form-field");
    expect(name).not.toHaveTextContent("EVAL.UNKNOWN_EVENT");
  });

  it("offers domain draft event names as suggestions", async () => {
    renderForm(<EvaluationForm />);
    await waitForContract();
    const list = document.getElementById(
      "evaluation-domain-events",
    ) as HTMLDataListElement;
    const options = Array.from(list.options).map((o) => o.value);
    expect(options).toEqual(
      expect.arrayContaining(["JobCompleted", "InvoiceIssued"]),
    );
  });

  it("re-syncs when the draft text changes outside the form", async () => {
    renderForm(
      <>
        <EvaluationForm />
        <DraftEditor file="evaluation.yaml" />
      </>,
    );
    await waitForContract();

    const textarea = screen.getByLabelText("evaluation.yaml draft YAML");
    const edited = draftText().replace(
      "completion-to-invoice-latency",
      "typed-in-raw-editor",
    );
    await userEvent.setup().click(textarea);
    // Replace the draft as a raw edit would (one change event).
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(textarea, { target: { value: edited } });
    expect(screen.getByLabelText("Name")).toHaveValue("typed-in-raw-editor");
  });

  it("an unparseable draft degrades to a notice, not a crash", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_FORM_OPEN_RESPONSE,
      files: {
        ...SYNTHETIC_FORM_OPEN_RESPONSE.files,
        "evaluation.yaml": "evaluation: [unclosed\n",
      },
    };
    renderForm(<EvaluationForm />, response);
    await waitFor(() =>
      expect(
        screen.getByText(/not a readable YAML mapping/),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByLabelText("Name")).toBeNull();
  });

  it("a file absent from the draft offers a skeleton", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_FORM_OPEN_RESPONSE,
      files: { ...SYNTHETIC_FORM_OPEN_RESPONSE.files },
    };
    delete response.files["evaluation.yaml"];
    const user = userEvent.setup();
    renderForm(<EvaluationForm />, response);

    await user.click(
      screen.getByRole("button", {
        name: "Create evaluation.yaml skeleton",
      }),
    );
    expect(draftText()).toContain("kind: evaluation");
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
  });

  it("editing under a scalar section repairs the draft instead of crashing", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_FORM_OPEN_RESPONSE,
      files: {
        ...SYNTHETIC_FORM_OPEN_RESPONSE.files,
        // Malformed draft: a scalar where the form expects a mapping.
        "evaluation.yaml":
          "schema_version: 1\nkind: evaluation\nevaluation:\n  name: x\n  baseline: 9\n",
      },
    };
    const user = userEvent.setup();
    renderForm(<EvaluationForm />, response);

    const input = screen.getByLabelText("Baseline value");
    await user.type(input, "5");
    await waitFor(() => expect(draftText()).toContain("value: 5"));
    expect(draftText()).toContain("baseline:");
  });

  it("a draft edit marks validation diagnostics stale, not invalid", () => {
    const opened = workspaceReducer(initialWorkspaceState, {
      type: "opened",
      requestedPath: "x",
      response: SYNTHETIC_FORM_OPEN_RESPONSE,
    });
    const validated = workspaceReducer(opened, {
      type: "validationFinished",
      requestId: 0,
      revision: 0,
      valid: false,
      digest: "d",
      diagnostics: [SYNTHETIC_DIAGNOSTIC],
    });
    const edited = workspaceReducer(validated, {
      type: "edited",
      file: "evaluation.yaml",
      text: "changed",
    });
    const { unmount } = render(
      <WorkspaceProvider initial={edited}>
        <TextField
          label="F"
          value=""
          commit={(t) => t}
          diagnostics={[SYNTHETIC_DIAGNOSTIC]}
          path="domain.entities.Job"
        />
        <FieldDiagnostics
          diagnostics={[SYNTHETIC_DIAGNOSTIC]}
          path="domain.entities.Job"
        />
      </WorkspaceProvider>,
    );
    expect(screen.getByLabelText("F")).not.toHaveAttribute("aria-invalid");
    expect(
      screen.getAllByText("(stale)").length,
    ).toBeGreaterThan(0);
    unmount();

    render(
      <WorkspaceProvider initial={validated}>
        <TextField
          label="F"
          value=""
          commit={(t) => t}
          diagnostics={[SYNTHETIC_DIAGNOSTIC]}
          path="domain.entities.Job"
        />
      </WorkspaceProvider>,
    );
    expect(screen.getByLabelText("F")).toHaveAttribute("aria-invalid", "true");
  });

  it("contract fetch failure degrades selects to usable text inputs", async () => {
    contractMock.mockRejectedValue(
      new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    );
    const user = userEvent.setup();
    renderForm(<EvaluationForm />);
    await waitFor(() =>
      expect(
        screen.getAllByText(/Contract vocabulary unavailable/).length,
      ).toBeGreaterThan(0),
    );

    const aggregation = screen.getByLabelText("Aggregation");
    expect(aggregation.tagName).toBe("INPUT");
    await user.clear(aggregation);
    await user.type(aggregation, "mean");
    expect(draftText()).toContain("aggregation: mean");
  });
});

describe("AgentForm", () => {
  function renderAgent(response?: OpenResponse) {
    return renderForm(<AgentForm />, response, "agent.yaml");
  }

  it("hydrates projection, task, and permitted actions from the draft", async () => {
    renderAgent();
    expect(screen.getByLabelText("Name")).toHaveValue(
      "billing-delay-diagnosis",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "explanation" }),
      ).toBeChecked(),
    );
    expect(screen.getByLabelText("Projection")).toHaveValue(
      "CompletedButNotInvoiced",
    );
    expect(screen.getByLabelText("Goal")).toHaveValue(
      "Identify likely causes of delayed invoicing and suggest an intervention.",
    );
    for (const name of [
      "explanation",
      "hypotheses",
      "follow_up_questions",
      "action_proposals",
    ]) {
      expect(screen.getByRole("checkbox", { name })).toBeChecked();
    }
    // One action card with a ref param and a string param.
    expect(screen.getByText("Action 1")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Action 1 param 1 ref"),
    ).toHaveValue("Person");
    expect(screen.getAllByRole("checkbox", { name: /required/i })).toHaveLength(
      2,
    );
  });

  it("never renders provider, model, or credential fields", async () => {
    renderAgent();
    await waitFor(() => expect(contractMock).toHaveBeenCalled());
    for (const el of screen.queryAllByRole("textbox")) {
      const label = el.closest(".form-field")?.textContent ?? "";
      expect(label).not.toMatch(/credential|api.?key|token|password|secret/i);
    }
  });

  it("contract failure keeps deliverables editable as free text", async () => {
    contractMock.mockRejectedValue(
      new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    );
    const user = userEvent.setup();
    renderAgent();
    await waitFor(() =>
      expect(
        screen.getAllByText(/Contract vocabulary unavailable/).length,
      ).toBeGreaterThan(0),
    );

    await user.type(
      screen.getByLabelText("New deliverable name"),
      "trace_report",
    );
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(draftText("agent.yaml")).toContain("trace_report"),
    );

    const removes = screen.getAllByRole("button", { name: "Remove" });
    await user.click(removes[0]);
    await waitFor(() =>
      expect(draftText("agent.yaml")).not.toContain("explanation"),
    );
  });

  it("toggling a deliverable edits the draft list", async () => {
    const user = userEvent.setup();
    renderAgent();
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "explanation" }),
      ).toBeChecked(),
    );

    await user.click(
      screen.getByRole("checkbox", { name: "follow_up_questions" }),
    );
    expect(draftText("agent.yaml")).not.toContain("follow_up_questions");
    expect(draftText("agent.yaml")).toContain("action_proposals");

    await user.click(
      screen.getByRole("checkbox", { name: "follow_up_questions" }),
    );
    expect(draftText("agent.yaml")).toContain("follow_up_questions");
  });

  it("adds, renames, and removes a permitted action", async () => {
    const user = userEvent.setup();
    renderAgent();
    await waitFor(() => expect(contractMock).toHaveBeenCalled());
    await screen.findByRole("checkbox", { name: "explanation" });

    await user.click(screen.getByRole("button", { name: "Add action" }));
    expect(draftText("agent.yaml")).toContain("NewAction");
    expect(screen.getByText("Action 2")).toBeInTheDocument();

    const actionCards = screen.getAllByText(/^Action \d$/);
    const second = actionCards[1].closest(".form-card");
    const nameInput = second?.querySelector(".form-field input");
    await user.clear(nameInput as HTMLElement);
    await user.type(nameInput as HTMLElement, "EscalateCase");
    expect(draftText("agent.yaml")).toContain("EscalateCase:");
    expect(draftText("agent.yaml")).toContain("SendReminder");

    await user.click(
      screen.getAllByRole("button", { name: "Remove action" })[1],
    );
    expect(draftText("agent.yaml")).not.toContain("EscalateCase");
    expect(draftText("agent.yaml")).toContain("SendReminder");
  });

  it("param editing shows ref field only for ref type and writes into the action", async () => {
    const user = userEvent.setup();
    renderAgent();
    await waitFor(() => expect(contractMock).toHaveBeenCalled());
    await screen.findByRole("checkbox", { name: "explanation" });

    // target param is type ref → ref field shows Person.
    expect(
      screen.getByLabelText("Action 1 param 1 ref"),
    ).toHaveValue("Person");

    // Change the 'reason' param type to ref → a second ref field appears.
    await user.selectOptions(
      screen.getByLabelText("Action 1 param 2 type"),
      "ref",
    );
    expect(draftText("agent.yaml")).toContain("reason:");
    const refFields = screen.getAllByLabelText(/param \d+ ref/);
    expect(refFields).toHaveLength(2);

    // Entity suggestions come from the domain draft.
    const list = document.getElementById(
      "agent-domain-entities",
    ) as HTMLDataListElement;
    expect(Array.from(list.options).map((o) => o.value)).toEqual(
      expect.arrayContaining(["Job", "Person"]),
    );
  });

  it("keeps permitted action content when the draft has unknown extras", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_FORM_OPEN_RESPONSE,
      files: {
        ...SYNTHETIC_FORM_OPEN_RESPONSE.files,
        "agent.yaml":
          SYNTHETIC_FORM_OPEN_RESPONSE.files["agent.yaml"].replace(
            "SendReminder:",
            "SendReminder:\n      unsupported_extra: true",
          ),
      },
    };
    const user = userEvent.setup();
    renderAgent(response);
    await waitFor(() => expect(contractMock).toHaveBeenCalled());
    await screen.findByRole("checkbox", { name: "explanation" });

    await user.click(screen.getByRole("checkbox", { name: "hypotheses" }));
    expect(draftText("agent.yaml")).toContain("unsupported_extra: true");
  });
});
