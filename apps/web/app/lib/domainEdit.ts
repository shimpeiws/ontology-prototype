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
import type { Document, Pair, YAMLMap } from "yaml";

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
 * Find a map pair by key. The `yaml` parser types unquoted plain scalars
 * (`123`, `true`) as number/boolean, while view names are always strings —
 * compare `String(key.value)` so `123:` matches the name "123".
 */
export function findPair(
  map: YAMLMap | undefined,
  key: string,
): Pair | undefined {
  if (!map) return undefined;
  return map.items.find(
    (p) => isScalar(p.key) && String(p.key.value) === key,
  );
}

/** Value node of a map pair addressed by a (possibly non-string) key. */
export function mapValue(
  map: YAMLMap | undefined,
  key: string,
): unknown {
  return findPair(map, key)?.value;
}

/** Remove a pair by normalized key; false when absent. */
export function deleteMapKey(
  map: YAMLMap | undefined,
  key: string,
): boolean {
  if (!map) return false;
  const i = map.items.findIndex(
    (p) => isScalar(p.key) && String(p.key.value) === key,
  );
  if (i < 0) return false;
  map.items.splice(i, 1);
  return true;
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
  const pair = findPair(map, oldKey);
  if (!pair) return false;
  if (findPair(map, newKey)) {
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

/**
 * The YAMLMap spec node for a declared entity/event/projection — created
 * when the declaration exists but its value is empty (`SomeEvent:`).
 * Throws when the collection or the declaration itself is absent, so a
 * mutator never silently produces unchanged text.
 */
function specMapFor(
  doc: Document,
  domain: YAMLMap,
  kind: "entities" | "events" | "projections",
  name: string,
): YAMLMap {
  const collection = mapValue(domain, kind);
  if (!isMap(collection)) {
    throw new Error(`'${kind}' is not a mapping`);
  }
  const pair = findPair(collection, name);
  if (!pair) throw new Error(`'${name}' not found in ${kind}`);
  if (!isMap(pair.value)) {
    pair.value = doc.createNode({});
  }
  return pair.value as YAMLMap;
}

/** Remove a concept from domain.yaml only — references in other draft files
 *  are left dangling so the validator reports them (see references.ts for
 *  the blast-radius scan shown before this runs). */
export function removeConcept(text: string, concept: Concept): string | null {
  return applyEdit(text, (doc, domain) => {
    switch (concept.kind) {
      case "entity":
      case "event":
      case "projection": {
        const collection = mapValue(
          domain,
          concept.kind === "entity"
            ? "entities"
            : concept.kind === "event"
              ? "events"
              : "projections",
        );
        if (!deleteMapKey(isMap(collection) ? collection : undefined, concept.name)) {
          throw new Error(`'${concept.name}' not found`);
        }
        break;
      }
      case "property": {
        const spec = specMapFor(doc, domain, concept.ownerKind, concept.owner);
        const props = mapValue(spec, "properties");
        if (
          !deleteMapKey(isMap(props) ? props : undefined, concept.name)
        ) {
          throw new Error(`property '${concept.name}' not found`);
        }
        break;
      }
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
    switch (concept.kind) {
      case "entity":
      case "event":
      case "projection": {
        const collection = mapValue(
          domain,
          concept.kind === "entity"
            ? "entities"
            : concept.kind === "event"
              ? "events"
              : "projections",
        );
        if (!renameMapKey(doc, isMap(collection) ? collection : undefined, concept.name, newName)) {
          throw new Error(`'${concept.name}' not found`);
        }
        break;
      }
      case "property": {
        const spec = specMapFor(doc, domain, concept.ownerKind, concept.owner);
        const props = mapValue(spec, "properties");
        if (
          !renameMapKey(
            doc,
            isMap(props) ? props : undefined,
            concept.name,
            newName,
          )
        ) {
          throw new Error(`property '${concept.name}' not found`);
        }
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
  return applyEdit(text, (doc, domain) => {
    const spec = specMapFor(doc, domain, owner.kind, owner.name);
    let props = mapValue(spec, "properties");
    if (!isMap(props)) {
      const created = doc.createNode({});
      spec.set("properties", created);
      props = created;
    }
    const propPair = findPair(props as YAMLMap, propName);
    if (!propPair) {
      throw new Error(`property '${propName}' not found`);
    }
    if (!isMap(propPair.value)) {
      propPair.value = doc.createNode({});
    }
    const pspec = propPair.value as YAMLMap;
    if (field === "required") {
      setOrDelete(pspec, "required", value === true ? true : null);
      return;
    }
    setOrDelete(pspec, field, value === "" ? null : value);
    if (field === "type" && value !== "ref") pspec.delete("ref");
  });
}

/** Set `subject` on an event (or projection) spec; null removes the key. */
export function setSubject(
  text: string,
  kind: "events" | "projections",
  name: string,
  subject: string | null,
): string | null {
  return applyEdit(text, (doc, domain) => {
    const spec = specMapFor(doc, domain, kind, name);
    setOrDelete(spec, "subject", subject);
  });
}

export function setSpecDescription(
  text: string,
  kind: "entities" | "events" | "projections",
  name: string,
  description: string,
): string | null {
  return applyEdit(text, (doc, domain) => {
    const spec = specMapFor(doc, domain, kind, name);
    setOrDelete(spec, "description", description === "" ? null : description);
  });
}
