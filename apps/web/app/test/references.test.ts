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
} from "./fixtures";

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
