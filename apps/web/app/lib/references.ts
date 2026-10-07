/**
 * Reference impact ("blast radius") for domain concepts across the draft
 * bundle.
 *
 * Positions mirror exactly what the core validators dereference
 * (packages/core domain.py / mapping.py / evaluation.py / agentdef.py):
 *
 * - entity names: `ref:` targets (domain properties, event properties,
 *   agent action params), event/projection `subject`, mapping entity
 *   bindings, mapping event `subject.entity`
 * - event names: mapping event bindings, evaluation
 *   `start_event`/`end_event`, projection `select` event lists and
 *   `elapsed.event`
 * - projection names: agent `projection`
 * - entity property names: mapping `properties` keys of bindings for that
 *   entity, `field` operands in projection `select.where` conditions over
 *   that entity
 *
 * This is a navigation/confirmation aid, not validation — the core's
 * diagnostics remain the only verdict on the draft. Files that are absent
 * from the draft or fail to parse are skipped and reported, never guessed
 * about (INV-005).
 */

import { isMap, isScalar, isSeq, parseDocument } from "yaml";
import type { Document, YAMLMap } from "yaml";

import type { Concept } from "./domainEdit";
import { mapValue, renameMapKey } from "./domainEdit";
import { WORKSPACE_FILE_NAMES } from "./types";
import type { WorkspaceFileName } from "./types";

export interface ReferenceHit {
  file: WorkspaceFileName;
  /** Dotted path in the same vocabulary the core diagnostics use. */
  path: string;
  /** Short description of what the reference means. */
  label: string;
}

export interface ScanResult {
  hits: ReferenceHit[];
  /** Draft files that could not be scanned (absent or unparseable). */
  skipped: WorkspaceFileName[];
}

type Drafts = Partial<Record<WorkspaceFileName, string | null>>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Compare a parsed scalar with an identifier name. YAML types unquoted
 * `123`/`true` as number/boolean while concept names are strings, so
 * `ref: 123` should still count as a reference to the entity shown as
 * "123" (the core will flag it as an invalid name — we only navigate).
 */
function scalarEq(value: unknown, name: string): boolean {
  return (
    (typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean") &&
    String(value) === name
  );
}

/** Parse a draft file to plain JS; undefined when absent or broken. */
function parseJS(text: string | null | undefined): unknown | undefined {
  if (text == null) return undefined;
  try {
    const doc = parseDocument(text);
    if (doc.errors.length > 0) return undefined;
    return doc.toJS();
  } catch {
    return undefined;
  }
}

function entries(value: unknown): [string, Record<string, unknown>][] {
  if (!isRecord(value)) return [];
  return Object.entries(value).flatMap(([k, v]) =>
    isRecord(v) ? [[k, v] as const] : [],
  );
}

function list(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

/* ---- scan (plain-JS walk of each draft file) ---- */

/** `field` operands appearing anywhere inside a raw `where` tree. */
function conditionFields(node: unknown, out: string[]): void {
  if (!isRecord(node)) return;
  if (typeof node.field === "string") out.push(node.field);
  if (Array.isArray(node.args)) {
    for (const arg of node.args) conditionFields(arg, out);
  }
  conditionFields(node.arg, out);
}

/**
 * Event/property references inside one projection's `select` block —
 * `required_events`/`absent_events` items and `elapsed.event` name events;
 * `where` fields name properties of the projection's subject entity.
 */
function scanSelect(
  proj: string,
  spec: Record<string, unknown>,
  concept: Concept,
  hits: ReferenceHit[],
): void {
  const select = isRecord(spec.select) ? spec.select : {};
  const base = `domain.projections.${proj}.select`;
  if (concept.kind === "event") {
    for (const key of ["required_events", "absent_events"] as const) {
      const names = Array.isArray(select[key]) ? select[key] : [];
      names.forEach((value, i) => {
        if (scalarEq(value, concept.name)) {
          hits.push({
            file: "domain.yaml",
            path: `${base}.${key}[${i}]`,
            label: `selection event of projection ${proj}`,
          });
        }
      });
    }
    const elapsed = isRecord(select.elapsed) ? select.elapsed : {};
    if (scalarEq(elapsed.event, concept.name)) {
      hits.push({
        file: "domain.yaml",
        path: `${base}.elapsed.event`,
        label: `elapsed anchor of projection ${proj}`,
      });
    }
  }
  if (
    concept.kind === "property" &&
    concept.ownerKind === "entities" &&
    scalarEq(spec.subject, concept.owner)
  ) {
    const fields: string[] = [];
    conditionFields(select.where, fields);
    if (fields.includes(concept.name)) {
      hits.push({
        file: "domain.yaml",
        path: `${base}.where`,
        label: `condition field '${concept.name}' of projection ${proj}`,
      });
    }
  }
}

function scanDomain(
  js: unknown,
  concept: Concept,
  hits: ReferenceHit[],
): void {
  const domain = isRecord(js) && isRecord(js.domain) ? js.domain : {};
  if (concept.kind === "event" || concept.kind === "property") {
    for (const [proj, spec] of entries(domain.projections)) {
      scanSelect(proj, spec, concept, hits);
    }
    return;
  }
  if (concept.kind !== "entity") return;
  for (const [entity, spec] of entries(domain.entities)) {
    for (const [prop, pspec] of entries(spec.properties)) {
      if (scalarEq(pspec.ref, concept.name)) {
        hits.push({
          file: "domain.yaml",
          path: `domain.entities.${entity}.properties.${prop}.ref`,
          label: `ref property '${prop}' on entity ${entity}`,
        });
      }
    }
  }
  for (const [event, spec] of entries(domain.events)) {
    if (scalarEq(spec.subject, concept.name)) {
      hits.push({
        file: "domain.yaml",
        path: `domain.events.${event}.subject`,
        label: `subject of event ${event}`,
      });
    }
    for (const [prop, pspec] of entries(spec.properties)) {
      if (scalarEq(pspec.ref, concept.name)) {
        hits.push({
          file: "domain.yaml",
          path: `domain.events.${event}.properties.${prop}.ref`,
          label: `ref property '${prop}' on event ${event}`,
        });
      }
    }
  }
  for (const [proj, spec] of entries(domain.projections)) {
    if (scalarEq(spec.subject, concept.name)) {
      hits.push({
        file: "domain.yaml",
        path: `domain.projections.${proj}.subject`,
        label: `subject of projection ${proj}`,
      });
    }
  }
}

function scanMapping(
  js: unknown,
  concept: Concept,
  hits: ReferenceHit[],
): void {
  const mapping = isRecord(js) && isRecord(js.mapping) ? js.mapping : {};
  list(mapping.entities).forEach((binding, i) => {
    if (concept.kind === "entity" && scalarEq(binding.entity, concept.name)) {
      hits.push({
        file: "mapping.yaml",
        path: `mapping.entities[${i}].entity`,
        label: `entity binding ${i + 1}`,
      });
    }
    if (
      concept.kind === "property" &&
      concept.ownerKind === "entities" &&
      scalarEq(binding.entity, concept.owner) &&
      isRecord(binding.properties) &&
      Object.hasOwn(binding.properties, concept.name)
    ) {
      hits.push({
        file: "mapping.yaml",
        path: `mapping.entities[${i}].properties.${concept.name}`,
        label: `property binding for ${binding.entity}.${concept.name}`,
      });
    }
  });
  list(mapping.events).forEach((binding, i) => {
    if (concept.kind === "event" && scalarEq(binding.event, concept.name)) {
      hits.push({
        file: "mapping.yaml",
        path: `mapping.events[${i}].event`,
        label: `event binding ${i + 1}`,
      });
    }
    if (
      concept.kind === "entity" &&
      isRecord(binding.subject) &&
      scalarEq(binding.subject.entity, concept.name)
    ) {
      hits.push({
        file: "mapping.yaml",
        path: `mapping.events[${i}].subject.entity`,
        label: `subject of event binding ${i + 1}`,
      });
    }
  });
}

function scanEvaluation(
  js: unknown,
  concept: Concept,
  hits: ReferenceHit[],
): void {
  if (concept.kind !== "event") return;
  const interval =
    isRecord(js) && isRecord(js.evaluation) && isRecord(js.evaluation.interval)
      ? js.evaluation.interval
      : {};
  for (const key of ["start_event", "end_event"] as const) {
    if (scalarEq(interval[key], concept.name)) {
      hits.push({
        file: "evaluation.yaml",
        path: `evaluation.interval.${key}`,
        label: `evaluation ${key}`,
      });
    }
  }
}

function scanAgent(
  js: unknown,
  concept: Concept,
  hits: ReferenceHit[],
): void {
  const agent = isRecord(js) && isRecord(js.agent) ? js.agent : {};
  if (concept.kind === "projection" && scalarEq(agent.projection, concept.name)) {
    hits.push({
      file: "agent.yaml",
      path: "agent.projection",
      label: "agent task projection",
    });
  }
  if (concept.kind !== "entity") return;
  for (const [action, spec] of entries(agent.permitted_actions)) {
    for (const [param, pspec] of entries(spec.params)) {
      if (scalarEq(pspec.ref, concept.name)) {
        hits.push({
          file: "agent.yaml",
          path: `agent.permitted_actions.${action}.params.${param}.ref`,
          label: `ref param '${param}' of action ${action}`,
        });
      }
    }
  }
}

export function findReferences(drafts: Drafts, concept: Concept): ScanResult {
  const hits: ReferenceHit[] = [];
  const skipped: WorkspaceFileName[] = [];
  const scanners: Record<
    WorkspaceFileName,
    (js: unknown, c: Concept, h: ReferenceHit[]) => void
  > = {
    "domain.yaml": scanDomain,
    "mapping.yaml": scanMapping,
    "evaluation.yaml": scanEvaluation,
    "agent.yaml": scanAgent,
  };
  for (const file of WORKSPACE_FILE_NAMES) {
    const js = parseJS(drafts[file]);
    if (js === undefined) {
      // A file present in the draft but unparseable may still hold
      // references we cannot see — report it rather than stay silent.
      if (drafts[file] != null) skipped.push(file);
      continue;
    }
    scanners[file](js, concept, hits);
  }
  return { hits, skipped };
}

/* ---- cross-file rename (YAML node edits) ---- */

function scalarIs(map: YAMLMap, key: string, value: string): boolean {
  return scalarEq(map.get(key), value);
}

/** Iterate `[path, valueMap]` pairs of a map-valued node. */
function specMaps(node: unknown): [unknown, YAMLMap][] {
  if (!isMap(node)) return [];
  return node.items.flatMap((p) =>
    isMap(p.value) ? [[p.key, p.value] as const] : [],
  );
}

/** Rewrite `key: old` to `key: newName` when the scalar value matches. */
function retarget(
  doc: Document,
  map: YAMLMap,
  key: string,
  oldName: string,
  newName: string,
): boolean {
  if (!scalarIs(map, key, oldName)) return false;
  map.set(key, doc.createNode(newName));
  return true;
}

/** Replace seq items that are scalar `oldName` with `newName`. */
function retargetSeqItems(
  doc: Document,
  node: unknown,
  oldName: string,
  newName: string,
): boolean {
  if (!isSeq(node)) return false;
  let changed = false;
  for (let i = 0; i < node.items.length; i++) {
    const item = node.items[i];
    if (isScalar(item) && scalarEq(item.value, oldName)) {
      node.set(i, doc.createNode(newName));
      changed = true;
    }
  }
  return changed;
}

/** Retarget `field:` scalars anywhere inside a `where` condition tree. */
function retargetConditionFields(
  doc: Document,
  node: unknown,
  oldName: string,
  newName: string,
): boolean {
  if (!isMap(node)) return false;
  let changed = retarget(doc, node, "field", oldName, newName);
  const args = node.get("args");
  if (isSeq(args)) {
    for (const arg of args.items) {
      if (retargetConditionFields(doc, arg, oldName, newName))
        changed = true;
    }
  }
  if (retargetConditionFields(doc, node.get("arg"), oldName, newName))
    changed = true;
  return changed;
}

/** Apply an event/property rename inside one projection spec's `select`. */
function retargetSelect(
  doc: Document,
  spec: YAMLMap,
  concept: Concept,
  newName: string,
): boolean {
  const select = spec.get("select");
  if (!isMap(select)) return false;
  let changed = false;
  if (concept.kind === "event") {
    for (const key of ["required_events", "absent_events"] as const) {
      if (
        retargetSeqItems(doc, select.get(key), concept.name, newName)
      )
        changed = true;
    }
    const elapsed = select.get("elapsed");
    if (
      isMap(elapsed) &&
      retarget(doc, elapsed, "event", concept.name, newName)
    )
      changed = true;
  } else if (
    concept.kind === "property" &&
    concept.ownerKind === "entities" &&
    scalarIs(spec, "subject", concept.owner)
  ) {
    if (
      retargetConditionFields(
        doc,
        select.get("where"),
        concept.name,
        newName,
      )
    )
      changed = true;
  }
  return changed;
}

function renamePropertyRefs(
  doc: Document,
  spec: YAMLMap,
  oldName: string,
  newName: string,
): boolean {
  const props = spec.get("properties");
  if (!isMap(props)) return false;
  let changed = false;
  for (const [, pspec] of specMaps(props)) {
    if (retarget(doc, pspec, "ref", oldName, newName)) changed = true;
  }
  return changed;
}

function renameInDomain(
  doc: Document,
  concept: Concept,
  newName: string,
): boolean {
  const domain = doc.get("domain");
  if (!isMap(domain)) return false;
  const entities = domain.get("entities");
  const events = domain.get("events");
  const projections = domain.get("projections");
  let changed = false;
  if (concept.kind === "entity") {
    // The declaration must be renamed first — a missing key means the
    // file cannot carry this rename, so abort rather than retarget
    // references to a name that is never declared.
    if (!isMap(entities) || !renameMapKey(doc, entities, concept.name, newName)) {
      throw new Error(`entity '${concept.name}' not found in domain.yaml`);
    }
    changed = true;
    for (const [, spec] of specMaps(entities)) {
      if (renamePropertyRefs(doc, spec, concept.name, newName))
        changed = true;
    }
    if (isMap(events)) {
      for (const [, spec] of specMaps(events)) {
        if (retarget(doc, spec, "subject", concept.name, newName))
          changed = true;
        if (renamePropertyRefs(doc, spec, concept.name, newName))
          changed = true;
      }
    }
    if (isMap(projections)) {
      for (const [, spec] of specMaps(projections)) {
        if (retarget(doc, spec, "subject", concept.name, newName))
          changed = true;
      }
    }
  } else if (concept.kind === "event") {
    if (!isMap(events) || !renameMapKey(doc, events, concept.name, newName)) {
      throw new Error(`event '${concept.name}' not found in domain.yaml`);
    }
    changed = true;
    // `select` blocks reference event names — retarget them too.
    for (const [, spec] of specMaps(projections)) {
      if (retargetSelect(doc, spec, concept, newName)) changed = true;
    }
  } else if (concept.kind === "projection") {
    if (
      !isMap(projections) ||
      !renameMapKey(doc, projections, concept.name, newName)
    ) {
      throw new Error(`projection '${concept.name}' not found in domain.yaml`);
    }
    changed = true;
  } else if (concept.kind === "property") {
    const collection =
      concept.ownerKind === "entities" ? entities : events;
    const spec = isMap(collection) ? mapValue(collection, concept.owner) : undefined;
    if (isMap(spec)) {
      const props = mapValue(spec, "properties");
      if (!isMap(props) || !renameMapKey(doc, props, concept.name, newName)) {
        throw new Error(
          `property '${concept.name}' not found in domain.yaml`,
        );
      }
      changed = true;
      // `where` conditions over this entity name the renamed property.
      for (const [, pspec] of specMaps(projections)) {
        if (retargetSelect(doc, pspec, concept, newName)) changed = true;
      }
    } else {
      throw new Error(
        `'${concept.owner}' not found in domain.yaml ${concept.ownerKind}`,
      );
    }
  }
  return changed;
}

function renameInMapping(
  doc: Document,
  concept: Concept,
  newName: string,
): boolean {
  const mapping = doc.get("mapping");
  if (!isMap(mapping)) return false;
  let changed = false;
  const entityBindings = mapping.get("entities");
  if (isSeq(entityBindings)) {
    for (const item of entityBindings.items) {
      if (!isMap(item)) continue;
      if (concept.kind === "entity") {
        if (retarget(doc, item, "entity", concept.name, newName))
          changed = true;
      } else if (
        concept.kind === "property" &&
        concept.ownerKind === "entities" &&
        scalarIs(item, "entity", concept.owner)
      ) {
        const props = item.get("properties");
        if (isMap(props) && renameMapKey(doc, props, concept.name, newName))
          changed = true;
      }
    }
  }
  const eventBindings = mapping.get("events");
  if (isSeq(eventBindings)) {
    for (const item of eventBindings.items) {
      if (!isMap(item)) continue;
      if (concept.kind === "event") {
        if (retarget(doc, item, "event", concept.name, newName))
          changed = true;
      } else if (concept.kind === "entity") {
        const subject = item.get("subject");
        if (isMap(subject) && retarget(doc, subject, "entity", concept.name, newName))
          changed = true;
      }
    }
  }
  return changed;
}

function renameInEvaluation(
  doc: Document,
  concept: Concept,
  newName: string,
): boolean {
  if (concept.kind !== "event") return false;
  const interval = doc.getIn(["evaluation", "interval"]);
  if (!isMap(interval)) return false;
  let changed = false;
  for (const key of ["start_event", "end_event"]) {
    if (retarget(doc, interval, key, concept.name, newName)) changed = true;
  }
  return changed;
}

function renameInAgent(
  doc: Document,
  concept: Concept,
  newName: string,
): boolean {
  const agent = doc.get("agent");
  if (!isMap(agent)) return false;
  let changed = false;
  if (concept.kind === "projection") {
    changed = retarget(doc, agent, "projection", concept.name, newName);
  } else if (concept.kind === "entity") {
    const actions = agent.get("permitted_actions");
    for (const [, action] of specMaps(actions)) {
      const params = action.get("params");
      for (const [, pspec] of specMaps(params)) {
        if (retarget(doc, pspec, "ref", concept.name, newName))
          changed = true;
      }
    }
  }
  return changed;
}

const RENAMERS: Record<
  WorkspaceFileName,
  (doc: Document, concept: Concept, newName: string) => boolean
> = {
  "domain.yaml": renameInDomain,
  "mapping.yaml": renameInMapping,
  "evaluation.yaml": renameInEvaluation,
  "agent.yaml": renameInAgent,
};

export interface RenameResult {
  /** File → new draft text, only for files that changed. */
  files: Partial<Record<WorkspaceFileName, string>>;
  /** Draft files with references possibly inside that could not be edited. */
  skipped: WorkspaceFileName[];
}

/**
 * Rename a concept in its declaration and at every known reference position
 * across the draft bundle. Absent files are ignored; present-but-unparseable
 * files are reported as skipped (their references stay dangling for the
 * validator to flag).
 */
export function applyRename(
  drafts: Drafts,
  concept: Concept,
  newName: string,
): RenameResult {
  const files: Partial<Record<WorkspaceFileName, string>> = {};
  const skipped: WorkspaceFileName[] = [];
  for (const file of WORKSPACE_FILE_NAMES) {
    const text = drafts[file];
    if (text == null) continue;
    let doc: Document;
    try {
      doc = parseDocument(text);
    } catch {
      skipped.push(file);
      continue;
    }
    if (doc.errors.length > 0) {
      skipped.push(file);
      continue;
    }
    try {
      // A renamer may throw when the new name collides with an existing
      // key in this file — the file stays untouched and is reported so
      // the result never silently holds a half-applied rename.
      if (RENAMERS[file](doc, concept, newName)) files[file] = doc.toString();
    } catch {
      skipped.push(file);
      // domain.yaml holds every declaration — if it cannot carry the
      // rename, retargeting other files would point references at a name
      // that is never declared. Abort the whole rename instead.
      if (file === "domain.yaml") break;
    }
  }
  return { files, skipped };
}
