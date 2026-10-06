/**
 * Structured editing over the `domain.yaml` draft text.
 *
 * The draft layer is byte-exact YAML text (see workspace/store.ts), so the
 * forms here parse the draft with `parseDocument` (CST-preserving), apply
 * mutations to the Document, and hand back `doc.toString()`. Comments, key
 * order, and content the forms do not model (e.g. `policies`, projection
 * details) survive an edit untouched (INV-002).
 *
 * Every mutator returns `null` when the draft cannot carry the edit
 * (syntax errors, missing `domain` mapping, an unexpected node kind) — the
 * caller keeps the raw editor as the fallback surface and never guesses.
 *
 * This module never decides validity: the core's diagnostics are the only
 * authority. Convenience reads are deliberately tolerant so a mid-edit
 * invalid draft still renders.
 */

import { isMap, isScalar, parseDocument } from "yaml";
import type { Document, YAMLMap } from "yaml";

export type OwnerKind = "entities" | "events";

/** A named thing the domain editor can rename or remove. */
export type Concept =
  | { kind: "entity" | "event" | "projection"; name: string }
  | {
      kind: "property";
      name: string;
      /** Which collection the owner lives in (entity or event spec). */
      ownerKind: OwnerKind;
      owner: string;
    };

export interface PropertyDraftView {
  name: string;
  type: string | null;
  required: boolean;
  ref: string | null;
  description: string | null;
}

export interface EntityDraftView {
  name: string;
  description: string | null;
  properties: PropertyDraftView[];
}

export interface EventDraftView {
  name: string;
  subject: string | null;
  description: string | null;
  properties: PropertyDraftView[];
}

export interface ProjectionDraftView {
  name: string;
  subject: string | null;
  description: string | null;
}

export interface DomainDraftView {
  name: string | null;
  description: string | null;
  policies: {
    entityIdType: string | null;
    occurrenceField: string | null;
    observationField: string | null;
    dateOnlyTimezone: string | null;
  } | null;
  entities: EntityDraftView[];
  events: EventDraftView[];
  projections: ProjectionDraftView[];
}

export interface ParsedDomainDraft {
  view: DomainDraftView | null;
  /** Human-facing reason the structured editor is unavailable. */
  problem: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strOrNull(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function mapEntries(value: unknown): [string, unknown][] {
  return isRecord(value) ? Object.entries(value) : [];
}

function propertyView(name: string, spec: unknown): PropertyDraftView {
  const s = isRecord(spec) ? spec : {};
  return {
    name,
    type: strOrNull(s.type),
    required: s.required === true,
    ref: strOrNull(s.ref),
    description: strOrNull(s.description),
  };
}

function propertiesOf(spec: unknown): PropertyDraftView[] {
  if (!isRecord(spec)) return [];
  return mapEntries(spec.properties).map(([name, s]) => propertyView(name, s));
}

export function parseDomainDraft(text: string): ParsedDomainDraft {
  const doc = parseDocument(text);
  if (doc.errors.length > 0) {
    return {
      view: null,
      problem: `domain.yaml has a YAML syntax error (${doc.errors[0].message})`,
    };
  }
  let js: unknown;
  try {
    js = doc.toJS();
  } catch {
    return {
      view: null,
      problem: "domain.yaml could not be read as data (unsupported alias structure?)",
    };
  }
  if (!isRecord(js) || !isRecord(js.domain)) {
    return {
      view: null,
      problem:
        "domain.yaml has no `domain` mapping — restore the required structure with the raw editor below.",
    };
  }
  const d = js.domain;
  const policies = isRecord(d.policies) ? d.policies : null;
  const times = policies && isRecord(policies.event_times) ? policies.event_times : null;
  return {
    view: {
      name: strOrNull(d.name),
      description: strOrNull(d.description),
      policies: policies
        ? {
            entityIdType: strOrNull(policies.entity_id_type),
            occurrenceField: times ? strOrNull(times.occurrence_field) : null,
            observationField: times ? strOrNull(times.observation_field) : null,
            dateOnlyTimezone: times ? strOrNull(times.date_only_timezone) : null,
          }
        : null,
      entities: mapEntries(d.entities).map(([name, spec]) => ({
        name,
        description: isRecord(spec) ? strOrNull(spec.description) : null,
        properties: propertiesOf(spec),
      })),
      events: mapEntries(d.events).map(([name, spec]) => ({
        name,
        subject: isRecord(spec) ? strOrNull(spec.subject) : null,
        description: isRecord(spec) ? strOrNull(spec.description) : null,
        properties: propertiesOf(spec),
      })),
      projections: mapEntries(d.projections).map(([name, spec]) => ({
        name,
        subject: isRecord(spec) ? strOrNull(spec.subject) : null,
        description: isRecord(spec) ? strOrNull(spec.description) : null,
      })),
    },
    problem: null,
  };
}

/* ---- Mutations ---- */

type Mutator = (doc: Document, domain: YAMLMap) => void;

function applyEdit(text: string, fn: Mutator): string | null {
  let doc: Document;
  try {
    doc = parseDocument(text);
  } catch {
    return null;
  }
  if (doc.errors.length > 0) return null;
  const domain = doc.get("domain");
  if (!isMap(domain)) return null;
  try {
    fn(doc, domain);
  } catch {
    return null;
  }
  return doc.toString();
}

/**
 * Rename a key of a YAML map, preserving its value node and position.
 * Returns false when `oldKey` is absent; throws when `newKey` already
 * exists in the same map — a duplicate key would corrupt the draft and
 * drop the structured editor entirely, so it is rejected, never written.
 */
export function renameMapKey(
  doc: Document,
  map: YAMLMap | undefined,
  oldKey: string,
  newKey: string,
): boolean {
  if (!map) return false;
  const pair = map.items.find(
    (p) => isScalar(p.key) && p.key.value === oldKey,
  );
  if (!pair) return false;
  if (
    map.items.some((p) => isScalar(p.key) && p.key.value === newKey)
  ) {
    throw new Error(`'${newKey}' already exists in this scope`);
  }
  pair.key = doc.createNode(newKey);
  return true;
}

/** Set `key: value` on a map node; `null` removes the key instead.
 *  Plain values are wrapped at stringify time — overwriting an existing
 *  scalar keeps its node (and comments) and re-quotes when needed. */
function setOrDelete(
  map: YAMLMap,
  key: string,
  value: string | boolean | null,
): void {
  if (value === null) {
    map.delete(key);
  } else {
    map.set(key, value);
  }
}

export function setDomainMeta(
  text: string,
  field: "name" | "description",
  value: string,
): string | null {
  return applyEdit(text, (doc, domain) => {
    // An empty description means "not set" — drop the key rather than
    // serializing an empty scalar.
    setOrDelete(domain, field, field === "description" && value === "" ? null : value);
  });
}

export function addEntity(text: string, name: string): string | null {
  return applyEdit(text, (doc) => {
    doc.setIn(["domain", "entities", name], doc.createNode({}));
  });
}

export function addEvent(text: string, name: string): string | null {
  return applyEdit(text, (doc) => {
    doc.setIn(["domain", "events", name], doc.createNode({}));
  });
}

/** Remove a concept from domain.yaml only — references in other draft files
 *  are left dangling so the validator reports them (see references.ts for
 *  the blast-radius scan shown before this runs). */
export function removeConcept(text: string, concept: Concept): string | null {
  return applyEdit(text, (doc) => {
    switch (concept.kind) {
      case "entity":
        doc.deleteIn(["domain", "entities", concept.name]);
        break;
      case "event":
        doc.deleteIn(["domain", "events", concept.name]);
        break;
      case "projection":
        doc.deleteIn(["domain", "projections", concept.name]);
        break;
      case "property":
        doc.deleteIn([
          "domain",
          concept.ownerKind,
          concept.owner,
          "properties",
          concept.name,
        ]);
        break;
    }
  });
}

/**
 * Rename only the declaration of a concept (its map key in domain.yaml).
 * Cross-file reference retargeting is `applyRename` in references.ts;
 * the UI offers this when the FDE chooses not to update references.
 */
export function renameDeclaration(
  text: string,
  concept: Concept,
  newName: string,
): string | null {
  return applyEdit(text, (doc, domain) => {
    void domain;
    switch (concept.kind) {
      case "entity":
      case "event":
      case "projection": {
        const collection =
          concept.kind === "entity"
            ? "entities"
            : concept.kind === "event"
              ? "events"
              : "projections";
        const map = doc.getIn(["domain", collection]);
        if (isMap(map)) renameMapKey(doc, map, concept.name, newName);
        break;
      }
      case "property": {
        const spec = doc.getIn(["domain", concept.ownerKind, concept.owner]);
        if (!isMap(spec)) return;
        const props = spec.get("properties");
        if (isMap(props)) renameMapKey(doc, props, concept.name, newName);
        break;
      }
    }
  });
}

export type PropertyField = "type" | "required" | "ref" | "description";

/** Sibling names a rename must not collide with, for UI gating and as a
 *  pre-flight check before applyRename. */
export function siblingsOf(
  view: DomainDraftView,
  concept: Concept,
): string[] {
  switch (concept.kind) {
    case "entity":
      return view.entities.map((e) => e.name);
    case "event":
      return view.events.map((e) => e.name);
    case "projection":
      return view.projections.map((p) => p.name);
    case "property": {
      const owner =
        concept.ownerKind === "entities"
          ? view.entities.find((e) => e.name === concept.owner)
          : view.events.find((e) => e.name === concept.owner);
      return (owner?.properties ?? []).map((p) => p.name);
    }
  }
}

export function addProperty(
  text: string,
  owner: { kind: OwnerKind; name: string },
  propName: string,
): string | null {
  return applyEdit(text, (doc) => {
    doc.setIn(
      ["domain", owner.kind, owner.name, "properties", propName],
      doc.createNode({}),
    );
  });
}

/**
 * Set one field of a property spec. Field rules:
 * - `type` away from `ref` drops the `ref` key (the core flags a stray `ref`).
 * - `required: false`, empty `ref`, and empty `description` delete the key
 *   rather than emitting noise values.
 */
export function setPropertyField(
  text: string,
  owner: { kind: OwnerKind; name: string },
  propName: string,
  field: PropertyField,
  value: string | boolean | null,
): string | null {
  return applyEdit(text, (doc) => {
    const specPath = ["domain", owner.kind, owner.name, "properties", propName];
    let spec = doc.getIn(specPath);
    if (!isMap(spec)) {
      doc.setIn(specPath, doc.createNode({}));
      spec = doc.getIn(specPath);
      if (!isMap(spec)) return;
    }
    if (field === "required") {
      setOrDelete(spec, "required", value === true ? true : null);
      return;
    }
    setOrDelete(spec, field, value === "" ? null : value);
    if (field === "type" && value !== "ref") spec.delete("ref");
  });
}

/** Set `subject` on an event (or projection) spec; null removes the key. */
export function setSubject(
  text: string,
  kind: "events" | "projections",
  name: string,
  subject: string | null,
): string | null {
  return applyEdit(text, (doc) => {
    const spec = doc.getIn(["domain", kind, name]);
    if (!isMap(spec)) return;
    setOrDelete(spec, "subject", subject);
  });
}

export function setSpecDescription(
  text: string,
  kind: "entities" | "events" | "projections",
  name: string,
  description: string,
): string | null {
  return applyEdit(text, (doc) => {
    const spec = doc.getIn(["domain", kind, name]);
    if (!isMap(spec)) return;
    setOrDelete(spec, "description", description === "" ? null : description);
  });
}
