/**
 * domainEdit tests — synthetic fixture mode (fixtures.ts). These exercise
 * the CST-preserving YAML mutations behind the structured domain editor:
 * views are tolerant, edits keep comments/unmodeled content, and hostile
 * names serialize safely (INV-002, INV-007).
 */

import { parseDocument } from "yaml";
import { describe, expect, it } from "vitest";

import {
  addEntity,
  addEvent,
  addProperty,
  parseDomainDraft,
  removeConcept,
  renameDeclaration,
  setDomainMeta,
  setPropertyField,
  setSpecDescription,
  setSubject,
  siblingsOf,
} from "../lib/domainEdit";
import { SYNTHETIC_DOMAIN_YAML } from "./fixtures";

function parseOk(text: string) {
  const parsed = parseDomainDraft(text);
  expect(parsed.view).not.toBeNull();
  return parsed.view!;
}

function js(text: string): Record<string, unknown> {
  return parseDocument(text).toJS() as Record<string, unknown>;
}

describe("parseDomainDraft", () => {
  it("builds a view of entities, events, projections, and policies", () => {
    const view = parseOk(SYNTHETIC_DOMAIN_YAML);
    expect(view.name).toBe("job-invoicing");
    expect(view.policies?.entityIdType).toBe("string");
    expect(view.policies?.occurrenceField).toBe("occurred_at");

    const job = view.entities.find((e) => e.name === "Job");
    expect(job?.description).toBe("A unit of work.");
    const assignee = job?.properties.find((p) => p.name === "assignee");
    expect(assignee).toMatchObject({
      type: "ref",
      ref: "Person",
      required: false,
    });
    expect(
      job?.properties.find((p) => p.name === "status")?.required,
    ).toBe(true);

    const issued = view.events.find((e) => e.name === "InvoiceIssued");
    expect(issued?.subject).toBe("Job");
    expect(
      issued?.properties.find((p) => p.name === "invoice"),
    ).toMatchObject({ type: "ref", ref: "Invoice" });

    expect(view.projections.map((p) => p.name)).toEqual([
      "CompletedButNotInvoiced",
    ]);
  });

  it("reports a problem instead of throwing on broken YAML", () => {
    const parsed = parseDomainDraft("domain: [unclosed");
    expect(parsed.view).toBeNull();
    expect(parsed.problem).toMatch(/syntax error/i);
  });

  it("reports a problem when the domain mapping is missing", () => {
    const parsed = parseDomainDraft("schema_version: 1\nkind: domain\n");
    expect(parsed.view).toBeNull();
    expect(parsed.problem).toMatch(/no `domain` mapping/);
  });

  it("still views a mid-edit invalid draft (type tolerated as-is)", () => {
    const text = SYNTHETIC_DOMAIN_YAML.replace(
      "{ type: string, required: true }",
      "{ type: 42 }",
    );
    const view = parseOk(text);
    const status = view.entities
      .find((e) => e.name === "Job")
      ?.properties.find((p) => p.name === "status");
    expect(status?.type).toBeNull();
  });
});

describe("domain metadata edits", () => {
  it("renames the domain and preserves the leading comment", () => {
    const next = setDomainMeta(SYNTHETIC_DOMAIN_YAML, "name", "billing");
    expect(next).not.toBeNull();
    expect(next).toContain("# edited in tests");
    expect(parseOk(next!).name).toBe("billing");
    expect(
      (js(next!).domain as Record<string, unknown>).name,
    ).toBe("billing");
  });

  it("empty description deletes the key rather than emitting ''", () => {
    const next = setDomainMeta(SYNTHETIC_DOMAIN_YAML, "description", "");
    expect((js(next!).domain as Record<string, unknown>).description)
      .toBeUndefined();
  });
});

describe("add/remove/rename", () => {
  it("addEntity creates a declaration the owner can grow", () => {
    const next = addEntity(SYNTHETIC_DOMAIN_YAML, "Invoice");
    const view = parseOk(next!);
    expect(view.entities.map((e) => e.name)).toContain("Invoice");
    // Unmodeled content survives untouched.
    expect(next).toContain("entity_id_type");
  });

  it("addEvent creates an event; setSubject binds it", () => {
    const added = addEvent(SYNTHETIC_DOMAIN_YAML, "JobCancelled")!;
    const bound = setSubject(added, "events", "JobCancelled", "Job")!;
    const view = parseOk(bound);
    expect(
      view.events.find((e) => e.name === "JobCancelled")?.subject,
    ).toBe("Job");
  });

  it("setSubject(null) removes the key", () => {
    const next = setSubject(
      SYNTHETIC_DOMAIN_YAML,
      "events",
      "JobCompleted",
      null,
    )!;
    const events = (js(next).domain as Record<string, Record<string, unknown>>)
      .events;
    expect(events.JobCompleted).toEqual({});
  });

  it("removeConcept drops the declaration; other concepts survive", () => {
    const next = removeConcept(SYNTHETIC_DOMAIN_YAML, {
      kind: "entity",
      name: "Person",
    })!;
    const view = parseOk(next);
    expect(view.entities.map((e) => e.name)).toEqual(["Job"]);
    // The assignee ref is left dangling for the validator to flag.
    expect(next).toContain("ref: Person");
  });

  it("removeConcept drops an event property", () => {
    const next = removeConcept(SYNTHETIC_DOMAIN_YAML, {
      kind: "property",
      ownerKind: "events",
      owner: "InvoiceIssued",
      name: "invoice",
    })!;
    expect(
      parseOk(next).events.find((e) => e.name === "InvoiceIssued")
        ?.properties,
    ).toEqual([]);
  });

  it("renameDeclaration refuses a name that collides with a sibling", () => {
    // Job -> Person would produce two `Person:` keys — rejected, not written.
    expect(
      renameDeclaration(
        SYNTHETIC_DOMAIN_YAML,
        { kind: "entity", name: "Job" },
        "Person",
      ),
    ).toBeNull();
    expect(
      renameDeclaration(
        SYNTHETIC_DOMAIN_YAML,
        {
          kind: "property",
          ownerKind: "entities",
          owner: "Job",
          name: "assignee",
        },
        "status",
      ),
    ).toBeNull();
  });

  it("siblingsOf lists the names a rename must not collide with", () => {
    const view = parseOk(SYNTHETIC_DOMAIN_YAML);
    expect(
      siblingsOf(view, { kind: "entity", name: "Job" }),
    ).toEqual(["Job", "Person"]);
    expect(
      siblingsOf(view, {
        kind: "property",
        ownerKind: "entities",
        owner: "Job",
        name: "status",
      }),
    ).toEqual(["status", "assignee"]);
  });

  it("renameDeclaration renames only the key, not references", () => {
    const next = renameDeclaration(
      SYNTHETIC_DOMAIN_YAML,
      { kind: "entity", name: "Person" },
      "Customer",
    )!;
    const view = parseOk(next);
    expect(view.entities.map((e) => e.name)).toEqual(["Job", "Customer"]);
    // The ref target is untouched — cross-file retargeting is a
    // separate, explicit operation (applyRename in references.ts).
    expect(next).toContain("ref: Person");
  });

  it("setSpecDescription edits an entity description", () => {
    const next = setSpecDescription(
      SYNTHETIC_DOMAIN_YAML,
      "entities",
      "Person",
      "A human being.",
    )!;
    expect(
      parseOk(next).entities.find((e) => e.name === "Person")?.description,
    ).toBe("A human being.");
  });
});

describe("property edits", () => {
  it("addProperty creates a spec the row controls can fill", () => {
    const added = addProperty(
      SYNTHETIC_DOMAIN_YAML,
      { kind: "entities", name: "Job" },
      "due_date",
    )!;
    const typed = setPropertyField(
      added,
      { kind: "entities", name: "Job" },
      "due_date",
      "type",
      "date",
    )!;
    const due = parseOk(typed)
      .entities.find((e) => e.name === "Job")
      ?.properties.find((p) => p.name === "due_date");
    expect(due?.type).toBe("date");
  });

  it("switching type away from ref drops the ref key", () => {
    const next = setPropertyField(
      SYNTHETIC_DOMAIN_YAML,
      { kind: "entities", name: "Job" },
      "assignee",
      "type",
      "string",
    )!;
    const assignee = parseOk(next)
      .entities.find((e) => e.name === "Job")
      ?.properties.find((p) => p.name === "assignee");
    expect(assignee).toMatchObject({ type: "string", ref: null });
  });

  it("required:false deletes the key instead of writing noise", () => {
    const next = setPropertyField(
      SYNTHETIC_DOMAIN_YAML,
      { kind: "entities", name: "Job" },
      "status",
      "required",
      false,
    )!;
    expect(
      parseOk(next).entities
        .find((e) => e.name === "Job")
        ?.properties.find((p) => p.name === "status")?.required,
    ).toBe(false);
    expect(next).not.toContain("required: false");
  });

  it("editing an event property works like an entity property", () => {
    const next = setPropertyField(
      SYNTHETIC_DOMAIN_YAML,
      { kind: "events", name: "InvoiceIssued" },
      "invoice",
      "description",
      "the raised invoice",
    )!;
    expect(
      parseOk(next)
        .events.find((e) => e.name === "InvoiceIssued")
        ?.properties.find((p) => p.name === "invoice")?.description,
    ).toBe("the raised invoice");
  });
});

describe("hostile input (INV-007)", () => {
  it("names needing quoting round-trip safely", () => {
    const weird = "has: colon";
    const next = addEntity(SYNTHETIC_DOMAIN_YAML, weird)!;
    const view = parseOk(next);
    expect(view.entities.map((e) => e.name)).toContain(weird);
    // And the emitted YAML parses back to the same key.
    const entities = (js(next).domain as Record<string, unknown>)
      .entities as Record<string, unknown>;
    expect(Object.keys(entities)).toContain(weird);
  });

  it("a mutator returns null instead of corrupting a broken draft", () => {
    expect(addEntity("domain: [unclosed", "X")).toBeNull();
    expect(
      setDomainMeta("no domain here\n", "name", "x"),
    ).toBeNull();
  });

  it("non-string YAML keys are found by string-normalized lookup", () => {
    // `123:` parses as a number key; the view shows "123" and mutations
    // must still reach it (core flags the invalid name, we only edit).
    const text = SYNTHETIC_DOMAIN_YAML.replace(
      "    Person:",
      "    123:\n      properties:\n        name: { type: string }\n    Person:",
    );
    const view = parseOk(text);
    expect(view.entities.map((e) => e.name)).toContain("123");

    const removed = removeConcept(text, { kind: "entity", name: "123" })!;
    expect(parseOk(removed).entities.map((e) => e.name)).toEqual([
      "Job",
      "Person",
    ]);

    const renamed = renameDeclaration(
      text,
      { kind: "entity", name: "123" },
      "Person2",
    )!;
    expect(
      parseOk(renamed).entities.map((e) => e.name),
    ).toContain("Person2");
  });

  it("mutators fail loudly (null) when the target is absent", () => {
    expect(
      removeConcept(SYNTHETIC_DOMAIN_YAML, {
        kind: "entity",
        name: "Nope",
      }),
    ).toBeNull();
    expect(
      setSubject(SYNTHETIC_DOMAIN_YAML, "events", "Nope", "Job"),
    ).toBeNull();
    expect(
      setPropertyField(
        SYNTHETIC_DOMAIN_YAML,
        { kind: "entities", name: "Job" },
        "nope",
        "type",
        "string",
      ),
    ).toBeNull();
  });

  it("adding an existing name fails instead of wiping its spec", () => {
    // setIn silently overwrites an existing key's value; the adders must
    // refuse so a desynced UI cannot clobber a populated declaration.
    expect(addEntity(SYNTHETIC_DOMAIN_YAML, "Job")).toBeNull();
    expect(addEvent(SYNTHETIC_DOMAIN_YAML, "JobCompleted")).toBeNull();
    expect(
      addProperty(
        SYNTHETIC_DOMAIN_YAML,
        { kind: "entities", name: "Job" },
        "status",
      ),
    ).toBeNull();
    const after = js(SYNTHETIC_DOMAIN_YAML);
    expect(
      (after.domain as Record<string, Record<string, unknown>>).entities
        .Job,
    ).toHaveProperty("properties");
  });
});
