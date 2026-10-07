/**
 * Mapping form + preview panel tests (#17) — synthetic fixture mode
 * (fixtures.ts): `api.contract` and `api.previewDraft` are mocked; drafts
 * live in the workspace store and are asserted through a probe, never
 * through a real API.
 */

import { useEffect } from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parseDocument } from "yaml";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../lib/api";
import { clearContractCache } from "../lib/contract";
import { useWorkspace, WorkspaceProvider } from "../workspace/context";
import type { OpenResponse, WorkspaceFileName } from "../lib/types";
import { MappingForm } from "../components/MappingForm";
import { PreviewPanel } from "../components/PreviewPanel";
import { DraftEditor } from "../components/DraftEditor";
import {
  SYNTHETIC_CONTRACT_RESPONSE,
  SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE,
  SYNTHETIC_MAPPING_FORM_YAML,
  SYNTHETIC_MULTI_SOURCE_OPEN_RESPONSE,
  SYNTHETIC_PREVIEW_RESPONSE,
} from "./fixtures";

vi.mock("../lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../lib/api")>();
  return {
    ...mod,
    api: {
      ...mod.api,
      contract: vi.fn(),
      previewDraft: vi.fn(),
    },
  };
});

const contractMock = vi.mocked(api.contract);
const previewMock = vi.mocked(api.previewDraft);

beforeEach(() => {
  clearContractCache();
  contractMock.mockResolvedValue(SYNTHETIC_CONTRACT_RESPONSE);
  previewMock.mockResolvedValue(SYNTHETIC_PREVIEW_RESPONSE);
});

afterEach(() => {
  contractMock.mockReset();
  previewMock.mockReset();
});

function Harness({
  children,
  response = SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE,
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

/** Applies a raw draft edit through the store — the same channel the raw
 *  DraftEditor uses — so tests can change the draft without a form. */
function EditProbe() {
  const { editFile } = useWorkspace();
  return (
    <button
      type="button"
      data-testid="bump-draft"
      onClick={() => editFile("mapping.yaml", "schema_version: 1\n")}
    />
  );
}

/** Reopens the provider on a different workspace — exercises revision
 *  reset, which a preview result keyed only on revision would miss. */
function SwitchProbe() {
  const { open } = useWorkspace();
  return (
    <button
      type="button"
      data-testid="switch-workspace"
      onClick={() =>
        open("other-workspace", SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE)
      }
    />
  );
}

function renderForm(
  children: React.ReactNode,
  response?: OpenResponse,
  file: WorkspaceFileName = "mapping.yaml",
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

function draftText(): string {
  return screen.getByTestId("draft-mapping.yaml").textContent ?? "";
}

/** The live draft as plain JS — assertions on stored scalar types. */
function draftJs(): any {
  return parseDocument(draftText()).toJS();
}

function card(label: string): HTMLElement {
  const el = screen.getByText(label).closest(".form-card");
  if (!el) throw new Error(`no form-card for ${label}`);
  return el as HTMLElement;
}

async function waitForContract() {
  await waitFor(() => expect(contractMock).toHaveBeenCalled());
  await screen.findByDisplayValue("csv").catch(() => undefined);
}

describe("MappingForm", () => {
  it("hydrates source, bindings, and conditions from the draft YAML", async () => {
    renderForm(<MappingForm />);
    await waitForContract();

    expect(screen.getByLabelText("Name")).toHaveValue("jobs-csv");
    expect(screen.getByLabelText("Source name")).toHaveValue("jobs");
    expect(screen.getByLabelText("Source format")).toHaveValue("csv");

    const job = card("Entity binding 1");
    expect(within(job).getByLabelText("Entity")).toHaveValue("Job");
    expect(within(job).getByLabelText("Entity id field")).toHaveValue("id");
    expect(
      within(job).getByLabelText("Property status field"),
    ).toHaveValue("status");

    const person = card("Entity binding 2");
    expect(within(person).getByLabelText("Entity")).toHaveValue("Person");
    expect(
      within(person).getByLabelText("Entity id field"),
    ).toHaveValue("assignee");

    const event1 = card("Event binding 1");
    expect(within(event1).getByLabelText("Event")).toHaveValue(
      "JobCompleted",
    );
    expect(within(event1).getByLabelText("Subject entity")).toHaveValue(
      "Job",
    );
    expect(
      within(event1).getByLabelText("occurred_at (occurrence) field"),
    ).toHaveValue("completed_at");
    expect(
      within(event1).getByLabelText("occurred_at (occurrence) cast (as)"),
    ).toHaveValue("datetime");

    // The `and` condition renders its two nested arguments.
    const ops = within(event1).getAllByLabelText("Operator");
    expect(ops.map((o) => (o as HTMLSelectElement).value)).toEqual([
      "and",
      "eq",
      "not_null",
    ]);

    // observed_at renders only on event binding 2.
    const event2 = card("Event binding 2");
    expect(
      within(event2).getByLabelText("observed_at (observation) field"),
    ).toHaveValue("observed_ts");
  });

  it("editing a field writes YAML to the draft and preserves comments", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    const name = screen.getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "renamed-mapping");
    expect(draftText()).toContain("name: renamed-mapping");
    expect(draftText()).toContain(
      "# comment proving form edits preserve untouched content",
    );
    expect(draftText()).toContain("schema_version: 1");
  });

  it("source format options come from the contract", async () => {
    renderForm(<MappingForm />);
    await waitForContract();
    const select = screen.getByLabelText("Source format");
    const options = Array.from(select.querySelectorAll("option")).map(
      (o) => o.value,
    );
    expect(options).toEqual(expect.arrayContaining(["csv", "json"]));
    expect(options).not.toContain("xml");
  });

  it("offers domain entity and event names as suggestions", async () => {
    renderForm(<MappingForm />);
    await waitForContract();
    const entities = document.getElementById(
      "mapping-domain-entities",
    ) as HTMLDataListElement;
    expect(Array.from(entities.options).map((o) => o.value)).toEqual(
      expect.arrayContaining(["Job", "Person"]),
    );
    const events = document.getElementById(
      "mapping-domain-events",
    ) as HTMLDataListElement;
    expect(Array.from(events.options).map((o) => o.value)).toEqual(
      expect.arrayContaining(["JobCompleted", "InvoiceIssued"]),
    );
  });

  it("adds and removes entity bindings; edits hit the survivor", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    await user.click(
      screen.getByRole("button", { name: "Add entity binding" }),
    );
    expect(screen.getByText("Entity binding 3")).toBeInTheDocument();
    expect(draftText()).toContain('entity: ""');

    await user.click(
      within(card("Entity binding 1")).getByRole("button", {
        name: "Remove entity binding",
      }),
    );
    expect(draftText()).not.toMatch(/- entity: Job\b/);
    // The event subjects still reference Job — only the binding went.
    expect(draftText()).toContain("subject: { entity: Job");
    expect(screen.getByText("Entity binding 2")).toBeInTheDocument();

    // Person is now binding 1; editing its entity name must not hit the
    // appended blank binding.
    const survivor = within(card("Entity binding 1")).getByLabelText(
      "Entity",
    );
    await user.clear(survivor);
    await user.type(survivor, "Worker");
    expect(draftText()).toContain("entity: Worker");
    expect(draftText()).toContain('entity: ""');
  });

  it("switching an assignment to literal writes `literal:`", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    const job = card("Entity binding 1");
    await user.selectOptions(
      within(job).getByLabelText("Entity id source"),
      "literal",
    );
    const literal = within(job).getByLabelText("Entity id literal");
    await user.type(literal, "fixed-id");
    await waitFor(() =>
      expect(draftText()).toMatch(/literal: '?fixed-id'?/),
    );
    // The entity's id no longer binds a source field.
    expect(draftText()).not.toMatch(/id: \{ field: id \}\n/);
  });

  it("adds a property assignment to an entity binding", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    const job = card("Entity binding 1");
    await user.type(
      within(job).getByLabelText("New property name"),
      "region",
    );
    await user.click(
      within(job).getByRole("button", { name: "Add property assignment" }),
    );
    expect(draftText()).toContain("region:");
    expect(draftText()).toMatch(/field: ""/);
  });

  it("changing a condition operator rewrites the node's skeleton", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    const event2 = card("Event binding 2");
    const op = within(event2).getByLabelText("Operator");
    expect(op).toHaveValue("not_null");
    await user.selectOptions(op, "is_null");
    await waitFor(() =>
      expect(draftText()).toContain("op: is_null"),
    );
  });

  it("adds a nested argument to an `and` condition", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    const event1 = card("Event binding 1");
    await user.click(
      within(event1).getByRole("button", { name: "Add argument" }),
    );
    expect((draftText().match(/op: eq/g) ?? []).length).toBe(2);
    expect(within(event1).getAllByLabelText("Operator")).toHaveLength(4);
  });

  it("removes a `when` condition from an event binding", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    const event2 = card("Event binding 2");
    await user.click(
      within(event2).getByRole("button", { name: "Remove condition" }),
    );
    // Only the `when` of the InvoiceIssued binding is gone — event 1 and
    // the occurred_at assignment using the same column survive.
    const tail = draftText().split("InvoiceIssued")[1];
    expect(tail).not.toContain("when:");
    expect(tail).toContain("invoiced_at");
    expect(draftText()).toContain("op: and");
  });

  it("occurrence cast options come from the contract's time_casts", async () => {
    renderForm(<MappingForm />);
    await waitForContract();
    const select = screen.getAllByLabelText(
      "occurred_at (occurrence) cast (as)",
    )[0];
    const options = Array.from(select.querySelectorAll("option")).map(
      (o) => o.value,
    );
    expect(options).toEqual(expect.arrayContaining(["date", "datetime"]));
    expect(options).not.toContain("string");
  });

  it("attaches field-level diagnostics to the owning assignment", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE,
      diagnostics: [
        {
          severity: "error",
          code: "MAPPING.MISSING_FIELD",
          message: "id assignment resolves no source field",
          file: "mapping.yaml",
          path: "mapping.entities[0].id.field",
          line: null,
          hint: null,
        },
      ],
    };
    renderForm(<MappingForm />, response);
    await waitForContract();

    const field = screen
      .getAllByLabelText("Entity id field")[0]
      .closest(".form-field");
    expect(field).toHaveTextContent("MAPPING.MISSING_FIELD");
    // A different binding's id field stays clean.
    const other = screen
      .getAllByLabelText("Entity id field")[1]
      .closest(".form-field");
    expect(other).not.toHaveTextContent("MAPPING.MISSING_FIELD");
  });

  it("contract failure degrades cast controls to free text, not hidden", async () => {
    contractMock.mockRejectedValue(
      new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    );
    renderForm(<MappingForm />);
    // Without a contract every select degrades to a text input that keeps
    // the draft's value visible and editable.
    const source = await screen.findByLabelText("Source format");
    expect(source.tagName).toBe("INPUT");
    expect(source).toHaveValue("csv");
    expect(
      screen.getByLabelText("Property status cast (as)"),
    ).toHaveValue("string");
    expect(
      screen.getAllByLabelText("occurred_at (occurrence) cast (as)")[0],
    ).toHaveValue("datetime");
  });

  it("a file absent from the draft offers a skeleton", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE,
      files: { ...SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE.files },
    };
    delete response.files["mapping.yaml"];
    const user = userEvent.setup();
    renderForm(<MappingForm />, response);

    await user.click(
      screen.getByRole("button", { name: "Create mapping.yaml skeleton" }),
    );
    expect(draftText()).toContain("kind: mapping");
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
  });

  it("an unparseable draft degrades to a notice, not a crash", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE,
      files: {
        ...SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE.files,
        "mapping.yaml": "mapping: [unclosed\n",
      },
    };
    renderForm(<MappingForm />, response);
    await waitFor(() =>
      expect(
        screen.getByText(/not a readable YAML mapping/),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByLabelText("Source name")).toBeNull();
  });

  it("repairs a scalar `mapping` section when adding bindings", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE,
      files: {
        ...SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE.files,
        "mapping.yaml": "schema_version: 1\nkind: mapping\nmapping: null\n",
      },
    };
    const user = userEvent.setup();
    renderForm(<MappingForm />, response);
    await waitForContract();

    await user.click(
      screen.getByRole("button", { name: "Add entity binding" }),
    );
    expect(screen.getByText("Entity binding 1")).toBeInTheDocument();
    expect(draftJs().mapping.entities).toHaveLength(1);

    await user.click(
      screen.getByRole("button", { name: "Add event binding" }),
    );
    expect(screen.getByText("Event binding 1")).toBeInTheDocument();
    expect(draftJs().mapping.events).toHaveLength(1);
  });

  it("keeps a numeric-looking id literal as a string", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    const job = card("Entity binding 1");
    await user.selectOptions(
      within(job).getByLabelText("Entity id source"),
      "literal",
    );
    // Ids are always strings — no type picker is offered here.
    expect(
      within(job).queryByLabelText("Entity id literal type"),
    ).toBeNull();
    await user.type(within(job).getByLabelText("Entity id literal"), "101");
    await waitFor(() =>
      expect(draftJs().mapping.entities[0].id.literal).toBe("101"),
    );
  });

  it("lets a property literal be a string or a JSON scalar", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    const job = card("Entity binding 1");
    await user.selectOptions(
      within(job).getByLabelText("Property status source"),
      "literal",
    );
    // String is the default type — a numeric-looking value stays a string.
    await user.type(
      within(job).getByLabelText("Property status literal"),
      "101",
    );
    await waitFor(() =>
      expect(
        draftJs().mapping.entities[0].properties.status.literal,
      ).toBe("101"),
    );

    // The explicit type switch stores the same text as a JSON scalar.
    await user.selectOptions(
      within(job).getByLabelText("Property status literal type"),
      "json",
    );
    await waitFor(() =>
      expect(
        draftJs().mapping.entities[0].properties.status.literal,
      ).toBe(101),
    );
  });

  it("keeps JSON literal mode through invalid intermediate input", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    const job = card("Entity binding 1");
    await user.selectOptions(
      within(job).getByLabelText("Property status source"),
      "literal",
    );
    // Choosing JSON on an empty literal must not snap back to string.
    await user.selectOptions(
      within(job).getByLabelText("Property status literal type"),
      "json",
    );
    expect(
      within(job).getByLabelText("Property status literal type"),
    ).toHaveValue("json");

    // `12.` is not valid JSON mid-edit; the draft holds it as a string
    // while the picker stays on JSON, and the finished `12.5` parses.
    await user.type(
      within(job).getByLabelText("Property status literal"),
      "12.5",
    );
    await waitFor(() =>
      expect(
        draftJs().mapping.entities[0].properties.status.literal,
      ).toBe(12.5),
    );
    expect(
      within(job).getByLabelText("Property status literal type"),
    ).toHaveValue("json");
  });

  it("renders one binding section per sources[] entry", async () => {
    renderForm(<MappingForm />, SYNTHETIC_MULTI_SOURCE_OPEN_RESPONSE);
    await waitForContract();

    const names = screen
      .getAllByLabelText("Source name")
      .map((el) => (el as HTMLInputElement).value);
    expect(names).toEqual(["jobs", "invoices"]);
    const formats = screen
      .getAllByLabelText("Source format")
      .map((el) => (el as HTMLSelectElement).value);
    expect(formats).toEqual(["csv", "json"]);

    const jobs = card("Source 1");
    expect(
      within(jobs).getByLabelText("Entity"),
    ).toHaveValue("Job");
    expect(
      within(jobs).getByLabelText("Event"),
    ).toHaveValue("JobCompleted");
    const invoices = card("Source 2");
    expect(
      within(invoices).getByLabelText("Entity"),
    ).toHaveValue("Invoice");
    expect(
      within(invoices).getByLabelText("Event"),
    ).toHaveValue("InvoiceIssued");
    // The legacy single-source header is gone — the draft's own shape wins.
    expect(screen.queryByRole("button", { name: "Convert to sources list" })).toBeNull();
  });

  it("edits a source entry without touching the other", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />, SYNTHETIC_MULTI_SOURCE_OPEN_RESPONSE);
    await waitForContract();

    const name = within(card("Source 2")).getByLabelText("Source name");
    await user.clear(name);
    await user.type(name, "billing");
    expect(draftJs().mapping.sources[1].name).toBe("billing");
    expect(draftJs().mapping.sources[0].name).toBe("jobs");

    // A binding edit inside the second source lands on sources[1].
    const entity = within(card("Source 2")).getByLabelText("Entity");
    await user.clear(entity);
    await user.type(entity, "Bill");
    expect(draftJs().mapping.sources[1].entities[0].entity).toBe("Bill");
    expect(draftJs().mapping.sources[0].entities[0].entity).toBe("Job");
  });

  it("adds and removes sources; edits hit the survivor", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />, SYNTHETIC_MULTI_SOURCE_OPEN_RESPONSE);
    await waitForContract();

    await user.click(
      screen.getByRole("button", { name: "Add source" }),
    );
    expect(draftJs().mapping.sources).toHaveLength(3);

    await user.click(
      within(card("Source 1")).getByRole("button", {
        name: "Remove source",
      }),
    );
    const sources = draftJs().mapping.sources;
    expect(sources.map((s: { name: string }) => s.name)).toEqual([
      "invoices",
      "",
    ]);
    // The invoices bindings survived — they were not nested under jobs.
    expect(sources[0].entities[0].entity).toBe("Invoice");
  });

  it("converts a single-source draft to the sources list", async () => {
    const user = userEvent.setup();
    renderForm(<MappingForm />);
    await waitForContract();

    await user.click(
      screen.getByRole("button", { name: "Convert to sources list" }),
    );

    const mapping = draftJs().mapping;
    expect(mapping.source).toBeUndefined();
    expect(mapping.entities).toBeUndefined();
    expect(mapping.events).toBeUndefined();
    expect(mapping.sources).toHaveLength(1);
    expect(mapping.sources[0].name).toBe("jobs");
    expect(mapping.sources[0].format).toBe("csv");
    expect(mapping.sources[0].entities).toHaveLength(2);
    expect(mapping.sources[0].events).toHaveLength(2);
    // Untouched content — the file-level comment — survives the rewrite.
    expect(draftText()).toContain(
      "# comment proving form edits preserve untouched content",
    );
  });

  it("attaches sources[] diagnostics to the owning binding", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_MULTI_SOURCE_OPEN_RESPONSE,
      diagnostics: [
        {
          severity: "error",
          code: "MAPPING.UNKNOWN_ENTITY",
          message: "unknown entity type 'Jbo'",
          file: "mapping.yaml",
          path: "mapping.sources[1].entities[0].entity",
          line: null,
          hint: null,
        },
      ],
    };
    renderForm(<MappingForm />, response);
    await waitForContract();

    const invoiceField = within(card("Source 2"))
      .getByLabelText("Entity")
      .closest(".form-field");
    expect(invoiceField).toHaveTextContent("MAPPING.UNKNOWN_ENTITY");
    const jobField = within(card("Source 1"))
      .getByLabelText("Entity")
      .closest(".form-field");
    expect(jobField).not.toHaveTextContent("MAPPING.UNKNOWN_ENTITY");
  });

  it("edits set-condition values as separate elements", async () => {
    const response: OpenResponse = {
      ...SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE,
      files: {
        ...SYNTHETIC_MAPPING_FORM_OPEN_RESPONSE.files,
        "mapping.yaml": SYNTHETIC_MAPPING_FORM_YAML.replace(
          "when: { op: not_null, field: invoiced_at }",
          'when: { op: in, field: status, values: ["New York, NY", "LA"] }',
        ),
      },
    };
    const user = userEvent.setup();
    renderForm(<MappingForm />, response);
    await waitForContract();

    const event2 = card("Event binding 2");
    // A comma inside one value must not split it into two entries.
    const first = within(event2).getByLabelText("Value 1");
    expect(first).toHaveValue("New York, NY");
    await user.type(first, "x");
    await waitFor(() =>
      expect(draftJs().mapping.events[1].when.values).toEqual([
        "New York, NYx",
        "LA",
      ]),
    );

    await user.click(
      within(event2).getByRole("button", { name: "Add value" }),
    );
    await waitFor(() =>
      expect(draftJs().mapping.events[1].when.values).toHaveLength(3),
    );
    await user.click(
      within(event2).getByRole("button", { name: "Remove value 2" }),
    );
    await waitFor(() =>
      expect(draftJs().mapping.events[1].when.values).toEqual([
        "New York, NYx",
        "",
      ]),
    );
  });
});

describe("PreviewPanel", () => {
  it("runs the preview with the draft bundle and sample, then renders evidence", async () => {
    const user = userEvent.setup();
    renderForm(<PreviewPanel />);
    await waitForContract();

    expect(
      screen.getByText(/selected source declares format csv/),
    ).toBeInTheDocument();
    expect(screen.getByText(/bounded to 262144 bytes/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText(/Sample input/),
      "id,status,assignee",
    );
    await user.click(screen.getByRole("button", { name: "Run preview" }));

    await waitFor(() => expect(previewMock).toHaveBeenCalledOnce());
    const [path, files, sample] = previewMock.mock.calls[0];
    expect(path).toBe("job-invoicing");
    expect(Object.keys(files)).toEqual(
      expect.arrayContaining([
        "domain.yaml",
        "mapping.yaml",
        "evaluation.yaml",
        "agent.yaml",
      ]),
    );
    expect(sample).toEqual({
      text: "id,status,assignee",
      source: "jobs",
    });

    // Header metadata: input/mapping versions and bounds.
    expect(await screen.findByText("prev-aaaabbbbcccc-dddd"))
      .toBeInTheDocument();
    expect(screen.getByText("sha256:sample")).toBeInTheDocument();
    expect(screen.getByText("sha256:mapping")).toBeInTheDocument();

    // Row → canonical output side by side, with nulls normalized.
    expect(screen.getByText("Record row 2")).toBeInTheDocument();
    // Job/101 appears both as a resolved entity and as the event subject.
    expect(screen.getAllByText("Job/101").length).toBeGreaterThan(0);
    expect(screen.getByText("JobCompleted")).toBeInTheDocument();
    expect(
      screen.getByText("2026-10-01T00:00:00Z"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("null").length).toBeGreaterThan(0);

    // Rejected row renders its row/field diagnostic.
    expect(screen.getByText("Record row 3")).toBeInTheDocument();
    expect(screen.getByText("rejected")).toBeInTheDocument();
    expect(screen.getByText("INGEST.MISSING_ID")).toBeInTheDocument();
  });

  it("offers source selection for multi-source drafts and sends it", async () => {
    const user = userEvent.setup();
    renderForm(<PreviewPanel />, SYNTHETIC_MULTI_SOURCE_OPEN_RESPONSE);
    await waitForContract();

    const select = screen.getByLabelText("Source");
    expect(select).toHaveValue("jobs");

    await user.type(screen.getByLabelText(/Sample input/), "a,b");
    await user.click(screen.getByRole("button", { name: "Run preview" }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledOnce());
    expect(previewMock.mock.calls[0][2]).toEqual({
      text: "a,b",
      source: "jobs",
    });
    expect(await screen.findByText("prev-aaaabbbbcccc-dddd"))
      .toBeInTheDocument();

    // Switching to the second source marks the shown result stale and
    // the next run sends that source's name.
    await user.selectOptions(select, "invoices");
    expect(await screen.findByText("stale")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run preview" }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));
    expect(previewMock.mock.calls[1][2]).toEqual({
      text: "a,b",
      source: "invoices",
    });
  });

  it("a single-source draft shows no source picker", async () => {
    renderForm(<PreviewPanel />);
    await waitForContract();

    expect(screen.queryByLabelText("Source")).toBeNull();
  });

  it("drops a stale source selection after a workspace switch", async () => {
    // FIND-007: a remembered source name must not leak into the request
    // after the draft stops declaring it — the picker disappears but the
    // next preview used to send the stale name (INGEST.UNKNOWN_SOURCE).
    const user = userEvent.setup();
    renderForm(
      <>
        <PreviewPanel />
        <SwitchProbe />
      </>,
      SYNTHETIC_MULTI_SOURCE_OPEN_RESPONSE,
    );
    await waitForContract();

    await user.selectOptions(screen.getByLabelText("Source"), "invoices");

    // Open a workspace that declares only "jobs".
    await user.click(screen.getByTestId("switch-workspace"));
    expect(screen.queryByLabelText("Source")).toBeNull();

    await user.type(screen.getByLabelText(/Sample input/), "a,b");
    await user.click(screen.getByRole("button", { name: "Run preview" }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledOnce());
    expect(previewMock.mock.calls[0][2]).toEqual({
      text: "a,b",
      source: "jobs",
    });
  });

  it("marks the result stale after a draft edit until rerun", async () => {
    const user = userEvent.setup();
    renderForm(
      <>
        <PreviewPanel />
        <EditProbe />
      </>,
    );
    await waitForContract();

    await user.type(screen.getByLabelText(/Sample input/), "a,b");
    await user.click(screen.getByRole("button", { name: "Run preview" }));
    expect(await screen.findByText("prev-aaaabbbbcccc-dddd"))
      .toBeInTheDocument();
    expect(screen.queryByText("stale")).toBeNull();

    await user.click(screen.getByTestId("bump-draft"));
    expect(await screen.findByText("stale")).toBeInTheDocument();

    // Re-running with the current draft clears the marker.
    await user.click(screen.getByRole("button", { name: "Run preview" }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByText("stale")).toBeNull(),
    );
  });

  it("marks the result stale after a confirmed revert (#53)", async () => {
    const user = userEvent.setup();
    renderForm(
      <>
        <PreviewPanel />
        <DraftEditor file="mapping.yaml" />
      </>,
    );
    await waitForContract();

    await user.type(screen.getByLabelText(/Sample input/), "a,b");
    await user.click(screen.getByRole("button", { name: "Run preview" }));
    expect(await screen.findByText("prev-aaaabbbbcccc-dddd"))
      .toBeInTheDocument();
    expect(screen.queryByText("stale")).toBeNull();

    // Edit the draft, then revert it through the real UI path: the revert
    // bumps the draft revision, so the shown result must go stale exactly
    // like an edit — never presented as current.
    fireEvent.change(screen.getByLabelText("mapping.yaml draft YAML"), {
      target: { value: "schema_version: 1\n" },
    });
    await user.click(
      screen.getByRole("button", { name: "Revert to active" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Discard changes" }),
    );
    expect(await screen.findByText("stale")).toBeInTheDocument();
  });

  it("changing the sample text marks the result stale", async () => {
    const user = userEvent.setup();
    renderForm(<PreviewPanel />);
    await waitForContract();

    const textarea = screen.getByLabelText(/Sample input/);
    await user.type(textarea, "a,b");
    await user.click(screen.getByRole("button", { name: "Run preview" }));
    expect(await screen.findByText("prev-aaaabbbbcccc-dddd"))
      .toBeInTheDocument();

    fireEvent.change(textarea, { target: { value: "a,b,c" } });
    expect(await screen.findByText("stale")).toBeInTheDocument();
  });

  it("marks the result stale when the workspace changes under it", async () => {
    const user = userEvent.setup();
    renderForm(
      <>
        <PreviewPanel />
        <SwitchProbe />
      </>,
    );
    await waitForContract();

    await user.type(screen.getByLabelText(/Sample input/), "a,b");
    await user.click(screen.getByRole("button", { name: "Run preview" }));
    expect(await screen.findByText("prev-aaaabbbbcccc-dddd"))
      .toBeInTheDocument();
    expect(screen.queryByText("stale")).toBeNull();

    // Opening another workspace resets draftRevision to 0 — the same
    // value this run was keyed to — so the workspace identity, not the
    // revision alone, must trip the stale marker.
    await user.click(screen.getByTestId("switch-workspace"));
    expect(await screen.findByText("stale")).toBeInTheDocument();
  });

  it("renders draft diagnostics when the bundle is invalid (preview null)", async () => {
    previewMock.mockResolvedValue({
      api_version: "v1",
      valid: false,
      digest: "sha256:bad",
      diagnostics: [
        {
          severity: "error",
          code: "MAPPING.UNKNOWN_PROPERTY",
          message: "entity 'Job' has no property 'owner'",
          file: "mapping.yaml",
          path: "mapping.entities[0].properties.owner",
          line: null,
          hint: null,
        },
      ],
      preview: null,
    });
    const user = userEvent.setup();
    renderForm(<PreviewPanel />);
    await waitForContract();

    await user.click(screen.getByRole("button", { name: "Run preview" }));
    expect(
      await screen.findByText(/did not validate/),
    ).toBeInTheDocument();
    expect(screen.getByText("MAPPING.UNKNOWN_PROPERTY")).toBeInTheDocument();
  });

  it("surfaces a transport failure as an error state with retry", async () => {
    previewMock.mockRejectedValue(
      new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    );
    const user = userEvent.setup();
    renderForm(<PreviewPanel />);
    await waitForContract();

    await user.click(screen.getByRole("button", { name: "Run preview" }));
    expect(await screen.findByText("WEB.API_UNREACHABLE"))
      .toBeInTheDocument();

    previewMock.mockResolvedValue(SYNTHETIC_PREVIEW_RESPONSE);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByText("prev-aaaabbbbcccc-dddd"),
    ).toBeInTheDocument();
  });

  it("warns when the sample exceeds the contract byte limit", async () => {
    renderForm(<PreviewPanel />);
    await waitForContract();

    const textarea = screen.getByLabelText(/Sample input/);
    fireEvent.change(textarea, { target: { value: "x".repeat(262145) } });
    expect(
      await screen.findByText(/over the 262144-byte limit/),
    ).toBeInTheDocument();
  });
});
