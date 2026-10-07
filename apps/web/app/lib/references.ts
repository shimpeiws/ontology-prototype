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

import { isAlias, isMap, isScalar, isSeq, parseDocument } from "yaml";
import type { Alias, Document, YAMLMap, YAMLSeq } from "yaml";

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

function scanMappingBindings(
  scope: unknown,
  base: string,
  concept: Concept,
  hits: ReferenceHit[],
): void {
  const bindings = isRecord(scope) ? scope : {};
  list(bindings.entities).forEach((binding, i) => {
    if (concept.kind === "entity" && scalarEq(binding.entity, concept.name)) {
      hits.push({
        file: "mapping.yaml",
        path: `${base}.entities[${i}].entity`,
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
        path: `${base}.entities[${i}].properties.${concept.name}`,
        label: `property binding for ${binding.entity}.${concept.name}`,
      });
    }
  });
  list(bindings.events).forEach((binding, i) => {
    if (concept.kind === "event" && scalarEq(binding.event, concept.name)) {
      hits.push({
        file: "mapping.yaml",
        path: `${base}.events[${i}].event`,
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
        path: `${base}.events[${i}].subject.entity`,
        label: `subject of event binding ${i + 1}`,
      });
    }
  });
}

function scanMapping(
  js: unknown,
  concept: Concept,
  hits: ReferenceHit[],
): void {
  const mapping = isRecord(js) && isRecord(js.mapping) ? js.mapping : {};
  scanMappingBindings(mapping, "mapping", concept, hits);
  list(mapping.sources).forEach((source, i) =>
    scanMappingBindings(source, `mapping.sources[${i}]`, concept, hits),
  );
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

/**
 * Resolve a YAML alias to its anchored node; other nodes pass through.
 * `findReferences` operates on `toJS()` output where aliases are already
 * resolved — the retarget side must resolve them too, or an aliased
 * reference is reported but never renamed. Read-only: never mutate a
 * dereferenced node, it may be shared through an anchor — use `editAt`
 * / `editItemAt` for writes.
 */
function deref(doc: Document, node: unknown): unknown {
  return isAlias(node) ? node.resolve(doc) : node;
}

/**
 * Run `edit` on the container at `map[key]`. When the position holds a
 * YAML alias, `edit` runs on a detached deep copy first and the alias is
 * replaced by the copy only if something actually changed — a shared
 * anchor (e.g. a `where` condition borrowed from another projection) is
 * never mutated in place.
 */
function editAt(
  doc: Document,
  map: YAMLMap,
  key: string,
  edit: (node: unknown) => boolean,
): boolean {
  const node = map.get(key, true);
  if (!isAlias(node)) return edit(node);
  const resolved = node.resolve(doc);
  if (resolved === undefined) return false;
  const copy = doc.createNode(resolved.toJS(doc));
  if (!edit(copy)) return false;
  map.set(key, copy);
  return true;
}

/** `editAt` for a seq item position. */
function editItemAt(
  doc: Document,
  seq: YAMLSeq,
  i: number,
  edit: (node: unknown) => boolean,
): boolean {
  const item = seq.items[i];
  if (!isAlias(item)) return edit(item);
  const resolved = item.resolve(doc);
  if (resolved === undefined) return false;
  const copy = doc.createNode(resolved.toJS(doc));
  if (!edit(copy)) return false;
  seq.set(i, copy);
  return true;
}

function scalarIs(doc: Document, map: YAMLMap, key: string, value: string): boolean {
  const node = deref(doc, map.get(key, true));
  return isScalar(node) && scalarEq(node.value, value);
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
  const node = map.get(key, true);
  const resolved = deref(doc, node);
  if (!isScalar(resolved) || !scalarEq(resolved.value, oldName)) return false;
  const rep = doc.createNode(newName);
  // Keep the anchor only when the position's own node carries it — a
  // resolved alias target's anchor belongs to the shared node elsewhere.
  if (isScalar(node) && node.anchor) rep.anchor = node.anchor;
  map.set(key, rep);
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
    const resolved = deref(doc, item);
    if (isScalar(resolved) && scalarEq(resolved.value, oldName)) {
      // Keep the anchor only when the item itself carries it; a bare
      // alias is replaced by a literal since the anchor lives elsewhere.
      const rep = doc.createNode(newName);
      if (isScalar(item) && item.anchor) rep.anchor = item.anchor;
      node.set(i, rep);
      changed = true;
    }
  }
  return changed;
}

/** True when `target` sits inside `root`'s own node tree — aliases are
 * not followed, so a target borrowed via `*alias` counts as outside. */
function insideSubtree(root: unknown, target: unknown): boolean {
  if (root === target) return true;
  if (isMap(root)) {
    return root.items.some((p) => insideSubtree(p.value, target));
  }
  if (isSeq(root)) {
    return root.items.some((i) => insideSubtree(i, target));
  }
  return false;
}

/** Drop every anchor name in a materialized subtree so the copy can
 * never redefine an anchor the original still owns. Alias nodes
 * themselves resolve by name and are left untouched. */
function stripAnchors(node: unknown): void {
  if (isMap(node)) {
    node.anchor = undefined;
    for (const p of node.items) {
      stripAnchors(p.key);
      stripAnchors(p.value);
    }
  } else if (isSeq(node)) {
    node.anchor = undefined;
    for (const i of node.items) stripAnchors(i);
  } else if (isScalar(node)) {
    node.anchor = undefined;
  }
}

/**
 * Replace an alias inside `root` whose target lives outside `spec` with
 * a materialized copy. Retargeting edits select subtrees in place, so
 * walking an alias into a shared target would either corrupt the
 * owning spec's anchored node or silently retarget another
 * projection's reference. Copies are walked too: an alias nested inside
 * a copied target still resolves through the document's anchors, i.e.
 * back to the original. `active` stops recursion through self-referring
 * anchors like `&a {x: *a}`.
 */
function materializeExternalAliases(
  doc: Document,
  root: unknown,
  spec: unknown,
  active: Set<unknown>,
): void {
  const materialize = (alias: Alias): unknown => {
    const resolved = alias.resolve(doc);
    if (
      resolved == null ||
      active.has(resolved) ||
      insideSubtree(spec, resolved)
    ) {
      return undefined;
    }
    const copy = resolved.clone();
    stripAnchors(copy);
    active.add(resolved);
    materializeExternalAliases(doc, copy, spec, active);
    active.delete(resolved);
    return copy;
  };
  if (isMap(root)) {
    for (const pair of root.items) {
      if (isAlias(pair.value)) {
        const copy = materialize(pair.value);
        if (copy !== undefined) pair.value = copy;
      } else {
        materializeExternalAliases(doc, pair.value, spec, active);
      }
    }
  } else if (isSeq(root)) {
    for (let i = 0; i < root.items.length; i++) {
      const item = root.items[i];
      if (isAlias(item)) {
        const copy = materialize(item);
        if (copy !== undefined) root.items[i] = copy;
      } else {
        materializeExternalAliases(doc, item, spec, active);
      }
    }
  }
}

/**
 * Break cross-spec alias sharing in a projection's `select` before a
 * rename walks it. Must run for every projection spec before any spec
 * is edited — detaching only the in-scope specs would still leak a
 * shared-target edit into out-of-scope aliases.
 */
function detachSelectAliases(doc: Document, spec: YAMLMap): void {
  const node = spec.get("select", true);
  if (isAlias(node)) {
    const resolved = node.resolve(doc);
    if (resolved == null) return;
    const copy = resolved.clone();
    stripAnchors(copy);
    materializeExternalAliases(doc, copy, spec, new Set([resolved]));
    spec.set("select", copy);
  } else {
    materializeExternalAliases(doc, node, spec, new Set());
  }
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
  if (
    editAt(doc, node, "args", (seq) => {
      if (!isSeq(seq)) return false;
      let c = false;
      for (let i = 0; i < seq.items.length; i++) {
        if (
          editItemAt(doc, seq, i, (m) =>
            retargetConditionFields(doc, m, oldName, newName),
          )
        )
          c = true;
      }
      return c;
    })
  )
    changed = true;
  if (
    editAt(doc, node, "arg", (m) =>
      retargetConditionFields(doc, m, oldName, newName),
    )
  )
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
  return editAt(doc, spec, "select", (select) => {
    if (!isMap(select)) return false;
    let changed = false;
    if (concept.kind === "event") {
      for (const key of ["required_events", "absent_events"] as const) {
        if (
          editAt(doc, select, key, (seq) =>
            retargetSeqItems(doc, seq, concept.name, newName),
          )
        )
          changed = true;
      }
      if (
        editAt(doc, select, "elapsed", (m) =>
          isMap(m) ? retarget(doc, m, "event", concept.name, newName) : false,
        )
      )
        changed = true;
    } else if (
      concept.kind === "property" &&
      concept.ownerKind === "entities" &&
      scalarIs(doc, spec, "subject", concept.owner)
    ) {
      if (
        editAt(doc, select, "where", (m) =>
          retargetConditionFields(doc, m, concept.name, newName),
        )
      )
        changed = true;
    }
    return changed;
  });
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
    // `select` blocks reference event names — retarget them too. Cross-
    // spec aliases are detached first so an in-place edit cannot leak
    // into a projection that only borrows the shared node.
    const specs = specMaps(projections);
    for (const [, spec] of specs) detachSelectAliases(doc, spec);
    for (const [, spec] of specs) {
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
      const pspecs = specMaps(projections);
      for (const [, spec] of pspecs) detachSelectAliases(doc, spec);
      for (const [, spec] of pspecs) {
        if (retargetSelect(doc, spec, concept, newName)) changed = true;
      }
    } else {
      throw new Error(
        `'${concept.owner}' not found in domain.yaml ${concept.ownerKind}`,
      );
    }
  }
  return changed;
}

/** Retarget bindings inside one binding scope (mapping or a source entry). */
function renameInBindingScope(
  doc: Document,
  scope: YAMLMap,
  concept: Concept,
  newName: string,
): boolean {
  let changed = false;
  const entityBindings = scope.get("entities");
  if (isSeq(entityBindings)) {
    for (const item of entityBindings.items) {
      if (!isMap(item)) continue;
      if (concept.kind === "entity") {
        if (retarget(doc, item, "entity", concept.name, newName))
          changed = true;
      } else if (
        concept.kind === "property" &&
        concept.ownerKind === "entities" &&
        scalarIs(doc, item, "entity", concept.owner)
      ) {
        const props = item.get("properties");
        if (isMap(props) && renameMapKey(doc, props, concept.name, newName))
          changed = true;
      }
    }
  }
  const eventBindings = scope.get("events");
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

function renameInMapping(
  doc: Document,
  concept: Concept,
  newName: string,
): boolean {
  const mapping = doc.get("mapping");
  if (!isMap(mapping)) return false;
  // Legacy top-level bindings plus every `sources[]` entry — the scanner
  // reports hits under both, so the rename must retarget both or the draft
  // keeps dangling names the validator flags as unknown.
  let changed = renameInBindingScope(doc, mapping, concept, newName);
  const sources = mapping.get("sources");
  if (isSeq(sources)) {
    for (const entry of sources.items) {
      if (!isMap(entry)) continue;
      if (renameInBindingScope(doc, entry, concept, newName)) changed = true;
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
