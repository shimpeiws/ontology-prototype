/**
 * Draft YAML document helpers for the structured definition forms (#18).
 *
 * A definition draft is a YAML text buffer in the workspace store. The
 * `yaml` package's Document API lets the forms mutate individual keys while
 * preserving everything the form does not expose — the schema_version/kind
 * header, comments, descriptions the parsed model drops (e.g.
 * baseline.description), and any other keys the core still validates.
 *
 * Nothing here validates: writes are faithful and every validity decision
 * stays with the shared core via draft validation.
 */

import { Document, isMap, isScalar, isSeq, parseDocument, Scalar } from "yaml";
import { YAMLMap, YAMLSeq } from "yaml";
import type { DomainDefinition } from "./types";

export type DraftDocState =
  /** The file is absent from the draft (removed, or not on disk). */
  | { status: "missing" }
  /** The draft text is not a usable YAML mapping — forms can't edit it. */
  | { status: "invalid"; message: string }
  | { status: "ready"; doc: Document };

/**
 * Parse draft text into a Document. Duplicate keys are permitted so a draft
 * the user is mid-way through fixing stays editable; the core still reports
 * them. A document with parse errors is rejected because `doc.toString()`
 * throws for it — the form must degrade, not crash.
 */
export function parseDraftDoc(text: string | null): DraftDocState {
  if (text === null) return { status: "missing" };
  if (text.trim() === "") return { status: "ready", doc: new Document({}) };
  let doc: Document;
  try {
    doc = parseDocument(text, { uniqueKeys: false });
  } catch (err) {
    return { status: "invalid", message: String(err) };
  }
  const first = doc.errors[0];
  if (first) return { status: "invalid", message: first.message };
  if (!isMap(doc.contents)) {
    return {
      status: "invalid",
      message: "the document root is not a mapping",
    };
  }
  return { status: "ready", doc };
}

/**
 * The `getIn`/`setIn`/`deleteIn` surface shared by `Document` and the
 * collection nodes — the commit helpers work at any depth, e.g. directly
 * on the map a `permitted_actions` pair points to.
 */
export interface NodeHost {
  getIn(path: readonly unknown[], keepScalar?: boolean): unknown;
  setIn(path: readonly unknown[], value: unknown): void;
  deleteIn(path: readonly unknown[]): boolean;
}

/** Display text for `getIn(path)` results and sequence items — Scalar
 *  nodes unwrap to their value, collections show a marker. */
export function scalarText(value: unknown): string {
  if (isScalar(value)) value = value.value;
  if (value === null || value === undefined) return "";
  if (typeof value === "object") {
    if (isMap(value)) return "{…}";
    if (isSeq(value)) return "[…]";
    return "";
  }
  return String(value);
}

/**
 * Repair intermediate nodes on `path` so writes never throw on a malformed
 * draft: a scalar where a mapping/sequence belongs (e.g. `baseline: 9`
 * under an edit of `baseline.value`) is replaced by an empty collection
 * instead of crashing `setIn`/`deleteIn`.
 */
export function ensureContainerPath(
  host: NodeHost,
  path: readonly (string | number)[],
): void {
  for (let i = 1; i < path.length; i += 1) {
    const parent = path.slice(0, i);
    const wantSeq = typeof path[i] === "number";
    const node = host.getIn(parent, true);
    if (wantSeq ? isSeq(node) : isMap(node)) continue;
    // setIn stores plain objects verbatim — a real collection node is
    // required for the next level of traversal to land.
    host.setIn(parent, wantSeq ? new YAMLSeq() : new YAMLMap());
  }
}

/**
 * Write a free-text field: an empty value deletes the key (the core reports
 * missing fields at the same path); otherwise the text is stored verbatim.
 * Returns the value's display text after the write.
 */
export function commitText(
  host: NodeHost,
  path: readonly (string | number)[],
  text: string,
): string {
  ensureContainerPath(host, path);
  if (text === "") host.deleteIn(path);
  else host.setIn(path, text);
  return scalarText(host.getIn(path));
}

/**
 * Write a numeric field: empty deletes the key; a finite parse stores the
 * number; anything else stores the string so core validation reports the
 * type error on the draft instead of the value being silently dropped.
 */
export function commitNumber(
  host: NodeHost,
  path: readonly (string | number)[],
  text: string,
): string {
  const trimmed = text.trim();
  ensureContainerPath(host, path);
  if (trimmed === "") {
    host.deleteIn(path);
  } else {
    const n = Number(trimmed);
    host.setIn(path, Number.isFinite(n) ? n : trimmed);
  }
  return scalarText(host.getIn(path));
}

/** Write a boolean field; `false` deletes the key (default is false). */
export function commitBool(
  host: NodeHost,
  path: readonly (string | number)[],
  checked: boolean,
): void {
  ensureContainerPath(host, path);
  if (checked) host.setIn(path, true);
  else host.deleteIn(path);
}

/** The YAMLMap at `path`, or null when absent or not a mapping. */
export function mapAt(
  host: NodeHost,
  path: readonly (string | number)[],
): YAMLMap | null {
  const node = host.getIn(path, true);
  return isMap(node) ? node : null;
}

/**
 * The YAMLMap at `path` on a Document, replacing or creating a non-map
 * value — for making a malformed node (e.g. a scalar where a mapping
 * belongs) editable again instead of throwing on `setIn`.
 */
export function ensureMap(
  doc: Document,
  path: readonly (string | number)[],
): YAMLMap {
  const existing = mapAt(doc, path);
  if (existing) return existing;
  const created = doc.createNode({});
  doc.setIn(path, created);
  return created as YAMLMap;
}

/** The YAMLSeq at `path`, or null when absent or not a list. */
export function seqAt(
  host: NodeHost,
  path: readonly (string | number)[],
): YAMLSeq | null {
  const node = host.getIn(path, true);
  return isSeq(node) ? node : null;
}

/** The YAMLSeq at `path` on a Document, creating it when absent. */
export function ensureSeq(
  doc: Document,
  path: readonly (string | number)[],
): YAMLSeq {
  const existing = seqAt(doc, path);
  if (existing) return existing;
  const created = doc.createNode([]);
  doc.setIn(path, created);
  return created as YAMLSeq;
}

/** `map.get(key)` when `node` is a YAMLMap, else undefined — for reading a
 *  member of a list item without assuming the draft is well-formed. */
export function memberValue(node: unknown, key: string): unknown {
  return isMap(node) ? node.get(key) : undefined;
}

/** Display text for a map key — parsed keys are Scalars, created keys are plain strings. */
export function keyText(key: unknown): string {
  if (isScalar(key)) return scalarText(key.value);
  return scalarText(key);
}

/** Rename the key of a map entry in place, keeping position and value. */
export function renamePairKey(map: YAMLMap, index: number, name: string): void {
  const pair = map.items[index];
  if (pair) pair.key = new Scalar(name);
}

/** Minimal valid-shape skeleton for a definition file absent from the draft. */
export function skeletonText(kind: "evaluation" | "agent"): string {
  return `schema_version: 1\nkind: ${kind}\n${kind}: {}\n`;
}

export interface DomainReferences {
  entities: string[];
  events: string[];
  projections: string[];
}

/**
 * Entity/event/projection names to offer as reference suggestions.
 *
 * The domain *draft* text wins so a name added in the domain editor is
 * usable here before validation runs; when the draft does not parse, the
 * last parsed definition bundle is the fallback. Suggestions only — the
 * fields stay free text and the core validates references.
 */
export function domainReferenceNames(
  draftText: string | null,
  fallback: DomainDefinition | null,
): DomainReferences {
  const parsed = draftText !== null ? parseDraftDoc(draftText) : null;
  if (parsed?.status === "ready") {
    const domain = mapAt(parsed.doc, ["domain"]);
    if (domain) {
      return {
        entities: childKeys(domain, "entities"),
        events: childKeys(domain, "events"),
        projections: childKeys(domain, "projections"),
      };
    }
    return { entities: [], events: [], projections: [] };
  }
  if (!fallback) return { entities: [], events: [], projections: [] };
  return {
    entities: Object.keys(fallback.entities),
    events: Object.keys(fallback.events),
    projections: Object.keys(fallback.projections),
  };
}

function childKeys(map: YAMLMap, key: string): string[] {
  const child = map.get(key, true);
  if (!isMap(child)) return [];
  return child.items.map((pair) => keyText(pair.key));
}
