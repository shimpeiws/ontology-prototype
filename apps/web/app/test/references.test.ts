/**
 * references tests — synthetic fixture mode (fixtures.ts). The scanner
 * mirrors the positions the core validators dereference; these tests pin
 * which positions count as references for each concept kind and how
 * applyRename retargets them across the draft bundle (INV-005).
 */

import { describe, expect, it } from "vitest";

import { applyRename, findReferences } from "../lib/references";
import type { Concept } from "../lib/domainEdit";
import {
  SYNTHETIC_AGENT_YAML,
  SYNTHETIC_BUNDLE_FILES,
  SYNTHETIC_EVALUATION_YAML,
  SYNTHETIC_MAPPING_YAML,
  SYNTHETIC_MULTI_SOURCE_MAPPING_YAML,
} from "./fixtures";

const MULTI_SOURCE_BUNDLE_FILES: Record<string, string> = {
  ...SYNTHETIC_BUNDLE_FILES,
  "mapping.yaml": SYNTHETIC_MULTI_SOURCE_MAPPING_YAML,
};

const BROKEN = "foo: [unclosed";

function paths(result: ReturnType<typeof findReferences>): string[] {
  return result.hits.map((h) => `${h.file}:${h.path}`).sort();
}

describe("findReferences", () => {
  it("an entity's blast radius spans all four files", () => {
    const result = findReferences(SYNTHETIC_BUNDLE_FILES, {
      kind: "entity",
      name: "Job",
    });
    expect(paths(result)).toEqual([
      "agent.yaml:agent.permitted_actions.SendReminder.params.related_entity.ref",
      "domain.yaml:domain.events.InvoiceIssued.subject",
      "domain.yaml:domain.events.JobCompleted.subject",
      "domain.yaml:domain.projections.CompletedButNotInvoiced.subject",
      "mapping.yaml:mapping.entities[0].entity",
      "mapping.yaml:mapping.events[0].subject.entity",
    ]);
    expect(result.skipped).toEqual([]);
  });

  it("an entity referenced only by ref properties still reports them", () => {
    const result = findReferences(SYNTHETIC_BUNDLE_FILES, {
      kind: "entity",
      name: "Person",
    });
    expect(paths(result)).toEqual([
      "agent.yaml:agent.permitted_actions.SendReminder.params.target.ref",
      "domain.yaml:domain.entities.Job.properties.assignee.ref",
      "mapping.yaml:mapping.entities[1].entity",
    ]);
  });

  it("an entity property concept matches mapping property keys", () => {
    const result = findReferences(SYNTHETIC_BUNDLE_FILES, {
      kind: "property",
      ownerKind: "entities",
      owner: "Job",
      name: "status",
    });
    expect(paths(result)).toEqual([
      "mapping.yaml:mapping.entities[0].properties.status",
    ]);
    // The 'status' property of a different entity is not matched.
    const unrelated = findReferences(SYNTHETIC_BUNDLE_FILES, {
      kind: "property",
      ownerKind: "entities",
      owner: "Person",
      name: "status",
    });
    expect(unrelated.hits).toEqual([]);
  });

  it("an event's blast radius covers bindings, interval, and select", () => {
    const result = findReferences(SYNTHETIC_BUNDLE_FILES, {
      kind: "event",
      name: "JobCompleted",
    });
    expect(paths(result)).toEqual([
      "domain.yaml:domain.projections.CompletedButNotInvoiced.select.elapsed.event",
      "domain.yaml:domain.projections.CompletedButNotInvoiced.select.required_events[0]",
      "evaluation.yaml:evaluation.interval.start_event",
      "mapping.yaml:mapping.events[0].event",
    ]);
  });

  it("a projection's blast radius is the agent task projection", () => {
    const result = findReferences(SYNTHETIC_BUNDLE_FILES, {
      kind: "projection",
      name: "CompletedButNotInvoiced",
    });
    expect(paths(result)).toEqual(["agent.yaml:agent.projection"]);
  });

  it("unparseable draft files are reported as skipped, never guessed", () => {
    const result = findReferences(
      { ...SYNTHETIC_BUNDLE_FILES, "mapping.yaml": BROKEN },
      { kind: "entity", name: "Job" },
    );
    expect(result.skipped).toEqual(["mapping.yaml"]);
    expect(
      result.hits.every((h) => h.file !== "mapping.yaml"),
    ).toBe(true);
  });

  it("absent files are simply not scanned", () => {
    const result = findReferences(
      { "domain.yaml": SYNTHETIC_BUNDLE_FILES["domain.yaml"] },
      { kind: "entity", name: "Job" },
    );
    expect(result.skipped).toEqual([]);
    expect(result.hits.length).toBeGreaterThan(0);
  });
});

describe("applyRename", () => {
  it("renames an entity declaration and every known reference", () => {
    const concept: Concept = { kind: "entity", name: "Person" };
    const result = applyRename(SYNTHETIC_BUNDLE_FILES, concept, "Customer");

    expect(Object.keys(result.files).sort()).toEqual([
      "agent.yaml",
      "domain.yaml",
      "mapping.yaml",
    ]);
    expect(result.files["domain.yaml"]).toContain("Customer:");
    expect(result.files["domain.yaml"]).toContain("ref: Customer");
    expect(result.files["domain.yaml"]).not.toContain("Person");
    expect(result.files["mapping.yaml"]).toContain("entity: Customer");
    expect(result.files["agent.yaml"]).toContain("ref: Customer");
    // Untouched file is not returned.
    expect(result.files["evaluation.yaml"]).toBeUndefined();
    expect(result.skipped).toEqual([]);
  });

  it("renames an event at its declaration, mapping binding, and interval", () => {
    const concept: Concept = { kind: "event", name: "JobCompleted" };
    const result = applyRename(SYNTHETIC_BUNDLE_FILES, concept, "JobDone");

    expect(result.files["domain.yaml"]).toContain("JobDone:");
    expect(result.files["domain.yaml"]).not.toContain("JobCompleted");
    expect(result.files["mapping.yaml"]).toContain("event: JobDone");
    expect(result.files["evaluation.yaml"]).toContain(
      "start_event: JobDone",
    );
    expect(result.files["agent.yaml"]).toBeUndefined();
  });

  it("renames an entity property in the domain and its mapping bindings", () => {
    const concept: Concept = {
      kind: "property",
      ownerKind: "entities",
      owner: "Job",
      name: "status",
    };
    const result = applyRename(SYNTHETIC_BUNDLE_FILES, concept, "state");

    expect(result.files["domain.yaml"]).toContain("state:");
    expect(result.files["domain.yaml"]).not.toContain("status");
    // Only the binding for entity Job is retargeted.
    expect(result.files["mapping.yaml"]).toContain(
      "state: { field: status }",
    );
    expect(result.files["agent.yaml"]).toBeUndefined();
  });

  it("renames an entity across every sources[] binding scope", () => {
    // FIND-005: the scanner reports hits under mapping.sources[i] but the
    // renamer originally walked only top-level bindings, leaving dangling
    // names in multi-source drafts.
    const concept: Concept = { kind: "entity", name: "Job" };
    const result = applyRename(
      MULTI_SOURCE_BUNDLE_FILES,
      concept,
      "WorkItem",
    );

    const mapping = result.files["mapping.yaml"]!;
    expect(mapping).not.toContain("entity: Job");
    expect(mapping).toContain("entity: WorkItem");
    // All three binding positions retargeted: sources[0] entity binding,
    // sources[0] event subject, sources[1] event subject.
    expect(mapping.match(/entity: WorkItem/g)).toHaveLength(3);
    // The domain keeps unrelated names (JobCompleted, job-invoicing) —
    // only Job's declaration key and its references retarget.
    expect(result.files["domain.yaml"]).toContain("WorkItem:");
    expect(result.files["domain.yaml"]).not.toContain("subject: Job");
    expect(result.files["domain.yaml"]).toContain("JobCompleted:");
    expect(result.files["agent.yaml"]).toContain("ref: WorkItem");
    expect(result.skipped).toEqual([]);
  });

  it("renames an event inside its sources[] binding", () => {
    const concept: Concept = { kind: "event", name: "InvoiceIssued" };
    const result = applyRename(
      MULTI_SOURCE_BUNDLE_FILES,
      concept,
      "Billed",
    );

    const mapping = result.files["mapping.yaml"]!;
    expect(mapping).toContain("event: Billed");
    expect(mapping).not.toContain("InvoiceIssued");
    expect(result.files["evaluation.yaml"]).toContain("end_event: Billed");
  });

  it("renames an entity property inside a sources[] binding", () => {
    const concept: Concept = {
      kind: "property",
      ownerKind: "entities",
      owner: "Job",
      name: "status",
    };
    const result = applyRename(
      MULTI_SOURCE_BUNDLE_FILES,
      concept,
      "state",
    );

    const mapping = result.files["mapping.yaml"]!;
    expect(mapping).toContain("state: { field: status }");
    expect(mapping).not.toContain("status: { field: status }");
  });

  it("renames a projection and the agent task reference", () => {
    const concept: Concept = {
      kind: "projection",
      name: "CompletedButNotInvoiced",
    };
    const result = applyRename(SYNTHETIC_BUNDLE_FILES, concept, "UnpaidJobs");
    expect(result.files["domain.yaml"]).toContain("UnpaidJobs:");
    expect(result.files["agent.yaml"]).toContain("projection: UnpaidJobs");
  });

  it("skips unparseable files but still renames the rest", () => {
    const concept: Concept = { kind: "entity", name: "Person" };
    const result = applyRename(
      { ...SYNTHETIC_BUNDLE_FILES, "agent.yaml": BROKEN },
      concept,
      "Customer",
    );
    expect(result.skipped).toEqual(["agent.yaml"]);
    expect(result.files["agent.yaml"]).toBeUndefined();
    expect(result.files["domain.yaml"]).toContain("Customer");
  });

  it("a no-reference rename only returns the declaring file", () => {
    const concept: Concept = { kind: "entity", name: "Invoice" };
    const drafts = {
      ...SYNTHETIC_BUNDLE_FILES,
      "domain.yaml":
        SYNTHETIC_BUNDLE_FILES["domain.yaml"]!.replace(
          "    Person:",
          "    Invoice:\n      properties:\n        total: { type: number }\n    Person:",
        ),
    };
    const result = applyRename(drafts, concept, "Bill");
    expect(Object.keys(result.files)).toEqual(["domain.yaml"]);
    expect(result.files["domain.yaml"]).toContain("Bill:");
  });

  it("a rename colliding with an existing key aborts the whole rename", () => {
    // Job -> Person: `Person:` already exists under entities, so
    // domain.yaml cannot carry the rename. Retargeting the other files
    // would point references at an undeclared merge — abort instead.
    const concept: Concept = { kind: "entity", name: "Job" };
    const result = applyRename(SYNTHETIC_BUNDLE_FILES, concept, "Person");
    expect(result.skipped).toEqual(["domain.yaml"]);
    expect(result.files).toEqual({});
  });

  it("retargets aliased event references inside select", () => {
    // `findReferences` works on toJS() where aliases are already resolved,
    // so an alias is reported as a hit — the rename must update it too.
    const drafts = {
      ...SYNTHETIC_BUNDLE_FILES,
      "domain.yaml": SYNTHETIC_BUNDLE_FILES["domain.yaml"]!.replace(
        "        absent_events: [InvoiceIssued]",
        "        absent_events: [*evt]",
      ).replace(
        "        required_events: [JobCompleted]",
        "        required_events: [&evt JobCompleted]",
      ),
    };
    const concept: Concept = { kind: "event", name: "JobCompleted" };
    const result = applyRename(drafts, concept, "JobDone");
    const text = result.files["domain.yaml"]!;
    // The anchored scalar is renamed and keeps its anchor, so *evt in
    // absent_events now resolves to JobDone — no dangling reference.
    expect(text).toContain("&evt JobDone");
    expect(text).toContain("*evt");
    expect(text).not.toContain("JobCompleted");
    // Round-trip: the scanner sees the renamed name through the alias.
    expect(
      paths(findReferences({ "domain.yaml": text }, { kind: "event", name: "JobDone" })),
    ).toEqual([
      "domain.yaml:domain.projections.CompletedButNotInvoiced.select.absent_events[0]",
      "domain.yaml:domain.projections.CompletedButNotInvoiced.select.elapsed.event",
      "domain.yaml:domain.projections.CompletedButNotInvoiced.select.required_events[0]",
    ]);
  });

  it("retargets an alias to an anchored non-reference scalar", () => {
    // The anchor lives on a description (not a reference position), so the
    // aliased list item itself must be replaced with a literal.
    const drafts = {
      ...SYNTHETIC_BUNDLE_FILES,
      "domain.yaml": SYNTHETIC_BUNDLE_FILES["domain.yaml"]!.replace(
        "  description: Minimal model.",
        "  description: &evt JobCompleted",
      ).replace(
        "        required_events: [JobCompleted]",
        "        required_events: [*evt]",
      ),
    };
    const concept: Concept = { kind: "event", name: "JobCompleted" };
    const result = applyRename(drafts, concept, "JobDone");
    const text = result.files["domain.yaml"]!;
    // The alias position now holds the literal new name; the anchored
    // description is not a reference and is left alone.
    expect(text).toContain("description: &evt JobCompleted");
    expect(
      paths(findReferences({ "domain.yaml": text }, { kind: "event", name: "JobDone" })),
    ).toEqual([
      "domain.yaml:domain.projections.CompletedButNotInvoiced.select.elapsed.event",
      "domain.yaml:domain.projections.CompletedButNotInvoiced.select.required_events[0]",
    ]);
  });

  it("detaches an aliased where condition instead of editing the shared anchor", () => {
    // PersonDone anchors a condition over its own subject's property;
    // CompletedButNotInvoiced borrows it via *filter. Renaming Job.status
    // must update only the Job projection's copy — mutating the anchored
    // node would corrupt the Person projection (status is a Person field
    // there).
    const drafts = {
      ...SYNTHETIC_BUNDLE_FILES,
      "domain.yaml": SYNTHETIC_BUNDLE_FILES["domain.yaml"]!.replace(
        `    CompletedButNotInvoiced:`,
        `    PersonDone:
      subject: Person
      select:
        required_events: [JobCompleted]
        where: &filter { op: eq, field: status, value: open }
    CompletedButNotInvoiced:`,
      ).replace(
        `        elapsed: { event: JobCompleted, comparator: gt, value: 2, unit: days }`,
        `        elapsed: { event: JobCompleted, comparator: gt, value: 2, unit: days }
        where: *filter`,
      ),
    };
    const concept: Concept = {
      kind: "property",
      ownerKind: "entities",
      owner: "Job",
      name: "status",
    };
    const result = applyRename(drafts, concept, "state");
    const text = result.files["domain.yaml"]!;
    // The anchored condition on PersonDone is untouched.
    expect(text).toContain("&filter { op: eq, field: status, value: open }");
    // The Job projection's alias position now holds a detached copy with
    // the renamed field.
    expect(text).toContain("field: state");
    // Person.status still resolves to PersonDone's condition alone.
    expect(
      paths(
        findReferences(
          { "domain.yaml": text },
          { kind: "property", ownerKind: "entities", owner: "Person", name: "status" },
        ),
      ),
    ).toEqual(["domain.yaml:domain.projections.PersonDone.select.where"]);
    expect(
      paths(
        findReferences(
          { "domain.yaml": text },
          { kind: "property", ownerKind: "entities", owner: "Job", name: "state" },
        ),
      ),
    ).toEqual([
      "domain.yaml:domain.projections.CompletedButNotInvoiced.select.where",
    ]);
  });

  const SHARED_WHERE_DOMAIN = `schema_version: 1
kind: domain
domain:
  name: t
  entities:
    Job:
      properties:
        status: { type: string }
    Person:
      properties:
        status: { type: string }
  projections:
    PersonOpen:
      subject: Person
      select:
        where: &filter { op: eq, field: status, value: open }
    JobOpen:
      subject: Job
      select:
        where: *filter
`;

  it("an aliased where retargets the copy, not the owner's anchor", () => {
    // JobOpen borrows PersonOpen's condition via *filter. Renaming
    // Job.status must retarget JobOpen's condition only — mutating the
    // shared target would leave PersonOpen naming a field Person lacks.
    const drafts = {
      ...SYNTHETIC_BUNDLE_FILES,
      "domain.yaml": SHARED_WHERE_DOMAIN,
    };
    const concept: Concept = {
      kind: "property",
      ownerKind: "entities",
      owner: "Job",
      name: "status",
    };
    const result = applyRename(drafts, concept, "state");
    const text = result.files["domain.yaml"]!;
    expect(text).toContain("&filter { op: eq, field: status, value: open }");
    expect(text).not.toContain("*filter");
    expect(text).toContain("where: { op: eq, field: state, value: open }");
    // JobOpen's materialized condition names the renamed property.
    expect(
      paths(
        findReferences(
          { "domain.yaml": text },
          { kind: "property", ownerKind: "entities", owner: "Job", name: "state" },
        ),
      ),
    ).toEqual(["domain.yaml:domain.projections.JobOpen.select.where"]);
  });

  it("an anchor rename leaves borrowed copies of other subjects alone", () => {
    // The other direction: renaming Person.status edits PersonOpen's
    // anchored condition while JobOpen — a different subject — keeps
    // its own field: status.
    const drafts = {
      ...SYNTHETIC_BUNDLE_FILES,
      "domain.yaml": SHARED_WHERE_DOMAIN,
    };
    const concept: Concept = {
      kind: "property",
      ownerKind: "entities",
      owner: "Person",
      name: "status",
    };
    const result = applyRename(drafts, concept, "state");
    const text = result.files["domain.yaml"]!;
    expect(text).toContain("&filter { op: eq, field: state, value: open }");
    expect(text).toContain("where: { op: eq, field: status, value: open }");
    // JobOpen still reports a reference to Job.status.
    expect(
      paths(
        findReferences(
          { "domain.yaml": text },
          { kind: "property", ownerKind: "entities", owner: "Job", name: "status" },
        ),
      ),
    ).toEqual(["domain.yaml:domain.projections.JobOpen.select.where"]);
  });

  const METRIC_DOMAIN = `schema_version: 1
kind: domain
domain:
  name: t
  entities:
    Ticket:
      properties:
        status: { type: string }
        parent: { type: ref, ref: Ticket }
    Resolution:
      properties:
        ticket: { type: ref, ref: Ticket }
        reopened: { type: boolean }
`;

  const METRIC_EVALUATION = `schema_version: 1
kind: evaluation
evaluation:
  interval: { start_event: Opened, end_event: Resolved }
  metrics:
    reopen_rate:
      entity: Resolution
      ref: ticket
      flag: reopened
    reopened_share:
      entity: Resolution
      ref: ticket
      where: &w { op: eq, field: reopened, value: true }
    parent_share:
      entity: Ticket
      ref: parent
      where: *w
`;

  it("metric declarations report entity, ref, flag, and where fields", () => {
    // Declared metrics dereference domain names the scanner must report:
    // the counted entity, and among its properties the ref, the flag,
    // and every `field` operand in a `where` tree.
    expect(
      paths(
        findReferences(
          { "evaluation.yaml": METRIC_EVALUATION },
          { kind: "entity", name: "Resolution" },
        ),
      ),
    ).toEqual([
      "evaluation.yaml:evaluation.metrics.reopen_rate.entity",
      "evaluation.yaml:evaluation.metrics.reopened_share.entity",
    ]);
    expect(
      paths(
        findReferences(
          { "evaluation.yaml": METRIC_EVALUATION },
          { kind: "entity", name: "Ticket" },
        ),
      ),
    ).toEqual(["evaluation.yaml:evaluation.metrics.parent_share.entity"]);
    expect(
      paths(
        findReferences(
          { "evaluation.yaml": METRIC_EVALUATION },
          {
            kind: "property",
            ownerKind: "entities",
            owner: "Resolution",
            name: "reopened",
          },
        ),
      ),
    ).toEqual([
      "evaluation.yaml:evaluation.metrics.reopen_rate.flag",
      "evaluation.yaml:evaluation.metrics.reopened_share.where",
    ]);
    expect(
      paths(
        findReferences(
          { "evaluation.yaml": METRIC_EVALUATION },
          {
            kind: "property",
            ownerKind: "entities",
            owner: "Resolution",
            name: "ticket",
          },
        ),
      ),
    ).toEqual([
      "evaluation.yaml:evaluation.metrics.reopen_rate.ref",
      "evaluation.yaml:evaluation.metrics.reopened_share.ref",
    ]);
    // `where` fields belong to the metric's entity — the borrowed
    // condition on parent_share names a Ticket property, not a
    // Resolution one.
    expect(
      paths(
        findReferences(
          { "evaluation.yaml": METRIC_EVALUATION },
          {
            kind: "property",
            ownerKind: "entities",
            owner: "Ticket",
            name: "reopened",
          },
        ),
      ),
    ).toEqual(["evaluation.yaml:evaluation.metrics.parent_share.where"]);
  });

  it("renames a counted entity inside metric declarations", () => {
    const result = applyRename(
      {
        "domain.yaml": METRIC_DOMAIN,
        "evaluation.yaml": METRIC_EVALUATION,
      },
      { kind: "entity", name: "Resolution" },
      "Outcome",
    );
    const text = result.files["evaluation.yaml"]!;
    expect(text.match(/entity: Outcome/g)).toHaveLength(2);
    expect(text).toContain("entity: Ticket");
  });

  it("renames a metric's ref, flag, and where field on its entity", () => {
    const result = applyRename(
      {
        "domain.yaml": METRIC_DOMAIN,
        "evaluation.yaml": METRIC_EVALUATION,
      },
      {
        kind: "property",
        ownerKind: "entities",
        owner: "Resolution",
        name: "reopened",
      },
      "reopened_flag",
    );
    const text = result.files["evaluation.yaml"]!;
    expect(text).toContain("flag: reopened_flag");
    expect(text).toContain("&w { op: eq, field: reopened_flag");
    // The borrowing metric counts a different entity: its detached copy
    // still names `reopened`, which is a Ticket field for it.
    expect(text).toContain("field: reopened,");
    expect(text).not.toContain("*w");
    expect(
      paths(
        findReferences(
          { "evaluation.yaml": text },
          {
            kind: "property",
            ownerKind: "entities",
            owner: "Resolution",
            name: "reopened_flag",
          },
        ),
      ),
    ).toEqual([
      "evaluation.yaml:evaluation.metrics.reopen_rate.flag",
      "evaluation.yaml:evaluation.metrics.reopened_share.where",
    ]);
    expect(
      paths(
        findReferences(
          { "evaluation.yaml": text },
          {
            kind: "property",
            ownerKind: "entities",
            owner: "Ticket",
            name: "reopened",
          },
        ),
      ),
    ).toEqual(["evaluation.yaml:evaluation.metrics.parent_share.where"]);
  });

  it("renames a metric's subject ref property", () => {
    const result = applyRename(
      {
        "domain.yaml": METRIC_DOMAIN,
        "evaluation.yaml": METRIC_EVALUATION,
      },
      {
        kind: "property",
        ownerKind: "entities",
        owner: "Resolution",
        name: "ticket",
      },
      "work_item",
    );
    const text = result.files["evaluation.yaml"]!;
    expect(text.match(/ref: work_item/g)).toHaveLength(2);
    expect(text).toContain("ref: parent");
  });

  it("does not touch a same-named scalar elsewhere", () => {
    // mapping `field: status` value must not be renamed — only keys.
    const concept: Concept = {
      kind: "property",
      ownerKind: "entities",
      owner: "Job",
      name: "status",
    };
    const result = applyRename(SYNTHETIC_BUNDLE_FILES, concept, "state");
    expect(result.files["mapping.yaml"]).toContain("field: status");
  });

  it("prototype property names do not create phantom binding hits", () => {
    // `in` matches inherited members; a property literally named
    // "toString" must not report a mapping.properties.toString binding.
    const concept: Concept = {
      kind: "property",
      ownerKind: "entities",
      owner: "Job",
      name: "toString",
    };
    const hits = findReferences(SYNTHETIC_BUNDLE_FILES, concept).hits;
    expect(hits.map((h) => h.path)).not.toContain(
      "mapping.entities[0].properties.toString",
    );
  });
});
