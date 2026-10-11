/**
 * Structured editor for `mapping.yaml` (#17): source selection, entity
 * bindings (id + property assignments), and event bindings (subject, `when`
 * condition, occurrence/observation times) in the restricted mapping
 * language.
 *
 * Every write goes through the draft YAML Document so comments, key order,
 * and unmodeled content survive; the shared core stays the only validator —
 * field-level diagnostics land on the owning control via the core's
 * `mapping.*` paths. Options come from `GET /api/v1/contract`; entity/event/
 * property suggestions come from the domain draft.
 */

import { useId, useRef, useState } from "react";
import { Button, Input, Select } from "antd";
import { isMap, isScalar, isSeq } from "yaml";
import type { Document, YAMLMap, YAMLSeq } from "yaml";

import { contractVocabulary, useContract } from "../lib/contract";
import type { ContractVocabulary } from "../lib/contract";
import {
  commitText,
  domainEntityProperties,
  domainEventTimeFields,
  ensureContainerPath,
  ensureSeq,
  keyText,
  mapAt,
  memberValue,
  scalarText,
  seqAt,
} from "../lib/draftDoc";
import type { Diagnostic, DomainDefinition } from "../lib/types";
import { useWorkspace } from "../workspace/context";
import {
  DraftDocGate,
  FieldDiagnostics,
  FormSection,
  SelectField,
  TextField,
  useDomainReferences,
  useDraftDoc,
  useFileDiagnostics,
} from "./FormControls";

const FILE = "mapping.yaml" as const;
const ROOT = "mapping";

/** Deeper condition trees stay editable in the raw YAML editor. */
const MAX_CONDITION_DEPTH = 6;

/** Read a scalar-ish display value out of a (possibly absent) YAML node. */
function nodeText(value: unknown): string {
  return scalarText(value);
}

/** Parse literal input: numbers/booleans/null stay typed, else string. */
function parseLiteral(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return "";
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return text;
  }
}

/** The assignment map at `path` on `host`, creating/replacing as needed. */
function ensureAssignment(
  host: YAMLMap,
  path: readonly (string | number)[],
  doc: Document,
): YAMLMap {
  ensureContainerPath(host, path);
  const existing = host.getIn(path, true);
  if (isMap(existing)) return existing;
  const created = doc.createNode({});
  host.setIn(path, created);
  return created as YAMLMap;
}

/** Which operand shape an operator takes — contract groups first, the
 *  language's fixed operator sets as fallback when metadata is absent. */
function opGroup(
  op: string,
  vocabulary: ContractVocabulary | null,
): "comparison" | "set" | "null" | "and_or" | "not" | null {
  const groups = vocabulary?.conditionOperators;
  const sets = {
    comparison: groups?.comparison ?? ["eq", "ne", "lt", "lte", "gt", "gte"],
    set: groups?.set ?? ["in", "not_in"],
    null: groups?.null ?? ["is_null", "not_null"],
    logic: groups?.logic ?? ["and", "or", "not"],
  };
  if (sets.comparison.includes(op)) return "comparison";
  if (sets.set.includes(op)) return "set";
  if (sets.null.includes(op)) return "null";
  if (op === "and" || op === "or") return "and_or";
  if (op === "not") return "not";
  return null;
}

function conditionSkeleton(op: string): Record<string, unknown> {
  switch (opGroup(op, null)) {
    case "comparison":
      return { op, field: "", value: "" };
    case "set":
      return { op, field: "", values: [] };
    case "null":
      return { op, field: "" };
    case "and_or":
      return { op, args: [{ op: "eq", field: "", value: "" }] };
    case "not":
      return { op, arg: { op: "is_null", field: "" } };
    default:
      return { op };
  }
}

/**
 * One `when` condition node of the restricted condition language.
 * `mutate` edits this node in place; `replace` swaps/removes it in the
 * parent slot — closures supplied by the parent so nested edits keep
 * locating their node by identity.
 */
function ConditionEditor({
  node,
  diagPath,
  diagnostics,
  vocabulary,
  mutate,
  replace,
  depth,
}: {
  node: unknown;
  diagPath: string;
  diagnostics: Diagnostic[];
  vocabulary: ContractVocabulary | null;
  mutate: (fn: (m: YAMLMap, d: Document) => void) => void;
  replace: (value: Record<string, unknown> | null) => void;
  depth: number;
}) {
  const cond = isMap(node) ? node : null;
  const op = cond ? scalarText(cond.get("op", true)) : "";
  const group = opGroup(op, vocabulary);
  const allOps = vocabulary
    ? [
        ...vocabulary.conditionOperators.comparison,
        ...vocabulary.conditionOperators.set,
        ...vocabulary.conditionOperators.null,
        ...vocabulary.conditionOperators.logic,
      ]
    : null;

  if (depth > MAX_CONDITION_DEPTH) {
    return (
      <p className="hint">
        This condition is nested too deeply for the form — edit it in the raw
        YAML editor below.
      </p>
    );
  }

  const textCommit =
    (key: "field") =>
    (text: string): string => {
      mutate((m) => {
        m.set(key, text);
      });
      return text;
    };

  const argsSeq = cond ? seqAt(cond, ["args"]) : null;
  const valuesNode = cond ? cond.get("values", true) : undefined;
  const valuesSeq = isSeq(valuesNode) ? valuesNode : null;

  return (
    <div className="condition-node">
      <div className="form-field">
        <SelectField
          label="Operator"
          value={op}
          options={allOps}
          commit={(text) => {
            replace(conditionSkeleton(text));
            return text;
          }}
          diagnostics={diagnostics}
          path={`${diagPath}.op`}
        />
      </div>

      {group === "comparison" || group === "set" || group === "null" ? (
        <TextField
          label="Field"
          value={cond ? nodeText(cond.get("field", true)) : ""}
          commit={textCommit("field")}
          diagnostics={diagnostics}
          path={`${diagPath}.field`}
        />
      ) : null}

      {group === "comparison" ? (
        <TextField
          label="Value"
          value={cond ? nodeText(cond.get("value", true)) : ""}
          commit={(text) => {
            mutate((m) => {
              m.set("value", parseLiteral(text));
            });
            return text;
          }}
          diagnostics={diagnostics}
          path={`${diagPath}.value`}
        />
      ) : null}

      {group === "set" ? (
        <div className="condition-values">
          {valuesSeq
            ? valuesSeq.items.map((item, i) => (
                <div className="condition-value" key={i}>
                  <TextField
                    label={`Value ${i + 1}`}
                    value={scalarText(item)}
                    commit={(text) => {
                      mutate((m, d) => {
                        const seq = seqAt(m, ["values"]);
                        const idx = seq ? seq.items.indexOf(item) : -1;
                        if (!seq || idx < 0) return;
                        seq.items[idx] = d.createNode(parseLiteral(text));
                      });
                      return text;
                    }}
                    diagnostics={diagnostics}
                    path={`${diagPath}.values[${i}]`}
                  />
                  <Button
                    onClick={() =>
                      mutate((m) => {
                        const seq = seqAt(m, ["values"]);
                        const idx = seq ? seq.items.indexOf(item) : -1;
                        if (seq && idx >= 0) seq.items.splice(idx, 1);
                      })
                    }
                  >
                    Remove value {i + 1}
                  </Button>
                </div>
              ))
            : null}
          {!valuesSeq && valuesNode !== undefined && valuesNode !== null ? (
            // A scalar where the list belongs — keep it visible; the next
            // edit rewrites it as a one-element list.
            <TextField
              label="Value 1"
              value={nodeText(valuesNode)}
              commit={(text) => {
                mutate((m, d) => {
                  m.set("values", d.createNode([parseLiteral(text)]));
                });
                return text;
              }}
              diagnostics={diagnostics}
              path={`${diagPath}.values`}
            />
          ) : null}
          <Button
            onClick={() =>
              mutate((m, d) => {
                const seq =
                  seqAt(m, ["values"]) ??
                  (() => {
                    const created = d.createNode([]) as YAMLSeq;
                    // A malformed scalar becomes the first element rather
                    // than being dropped.
                    const prev = m.get("values", true);
                    if (prev !== undefined && prev !== null) created.add(prev);
                    m.set("values", created);
                    return created;
                  })();
                seq.add(d.createNode(""));
              })
            }
          >
            Add value
          </Button>
          <FieldDiagnostics
            diagnostics={diagnostics}
            path={`${diagPath}.values`}
          />
        </div>
      ) : null}

      {group === "and_or" ? (
        <div className="condition-args">
          {(argsSeq?.items ?? []).map((child, j) => (
            <ConditionEditor
              key={j}
              node={child}
              diagPath={`${diagPath}.args[${j}]`}
              diagnostics={diagnostics}
              vocabulary={vocabulary}
              depth={depth + 1}
              mutate={(fn) =>
                mutate((m, d) => {
                  const seq = seqAt(m, ["args"]);
                  const idx = seq ? seq.items.indexOf(child) : -1;
                  if (!seq || idx < 0) return;
                  if (!isMap(seq.items[idx])) {
                    seq.items[idx] = d.createNode({});
                  }
                  fn(seq.items[idx] as YAMLMap, d);
                })
              }
              replace={(value) =>
                mutate((m, d) => {
                  const seq = seqAt(m, ["args"]);
                  const idx = seq ? seq.items.indexOf(child) : -1;
                  if (!seq || idx < 0) return;
                  if (value === null) seq.items.splice(idx, 1);
                  else seq.items[idx] = d.createNode(value);
                })
              }
            />
          ))}
          <Button
            onClick={() =>
              mutate((m, d) => {
                const seq =
                  seqAt(m, ["args"]) ??
                  (() => {
                    const created = d.createNode([]);
                    m.set("args", created);
                    return created;
                  })();
                seq.add(d.createNode({ op: "eq", field: "", value: "" }));
              })
            }
          >
            Add argument
          </Button>
        </div>
      ) : null}

      {group === "not" ? (
        <ConditionEditor
          node={cond ? cond.getIn(["arg"], true) : undefined}
          diagPath={`${diagPath}.arg`}
          diagnostics={diagnostics}
          vocabulary={vocabulary}
          depth={depth + 1}
          mutate={(fn) =>
            mutate((m, d) => fn(ensureAssignment(m, ["arg"], d), d))
          }
          replace={(value) =>
            mutate((m, d) => {
              if (value === null) m.delete("arg");
              else m.set("arg", d.createNode(value));
            })
          }
        />
      ) : null}

      <Button onClick={() => replace(null)}>Remove condition</Button>
      <FieldDiagnostics diagnostics={diagnostics} path={diagPath} deep />
    </div>
  );
}

/**
 * One `{field|literal, as}` assignment. `casts` is null where casts are
 * forbidden (ids/subjects), a vocabulary list elsewhere — the event-time
 * positions get the restricted `time_casts` list.
 *
 * `literalKind` controls how literal text is stored: `"string"` keeps the
 * typed text verbatim (ids, subjects, and event times — the mapping
 * language requires string literals there), while `"scalar"` lets the
 * author pick a string or a JSON scalar explicitly so a numeric-looking
 * string stays a string.
 */
function AssignmentFields({
  label,
  node,
  diagPath,
  diagnostics,
  casts,
  literalKind,
  edit,
}: {
  label: string;
  /** Render-time assignment node (YAMLMap or something malformed). */
  node: unknown;
  diagPath: string;
  diagnostics: Diagnostic[];
  casts: string[] | null;
  literalKind: "string" | "scalar";
  edit: (fn: (a: YAMLMap, d: Document) => void) => void;
}) {
  const assign = isMap(node) ? node : null;
  const mode = assign?.has("field")
    ? "field"
    : assign?.has("literal")
      ? "literal"
      : "unset";
  const fieldText = assign ? scalarText(assign.get("field", true)) : "";
  const literalText = assign ? scalarText(assign.get("literal", true)) : "";
  const literalNode = assign ? assign.get("literal", true) : undefined;
  const literalValue = isScalar(literalNode) ? literalNode.value : literalNode;
  // The stored literal's own type seeds the editor mode; an unset/empty
  // literal starts as a string.
  const storedKind: "string" | "json" =
    !assign?.has("literal") || typeof literalValue === "string"
      ? "string"
      : "json";
  // The picked mode is tracked apart from the stored value so an invalid
  // intermediate JSON scalar (`12.`) doesn't snap the picker back to
  // string mid-edit. `lastKindSync` records what our own commits stored —
  // the same scheme TextField uses — so only external draft changes
  // re-derive the mode from the stored value.
  const [literalType, setLiteralType] = useState<"string" | "json">(storedKind);
  const lastKindSync = useRef(storedKind);
  if (storedKind !== lastKindSync.current) {
    lastKindSync.current = storedKind;
    setLiteralType(storedKind);
  }
  const castText = assign ? scalarText(assign.get("as", true)) : "";
  const id = useId();

  return (
    <div className="assignment">
      <div className="form-field">
        <label htmlFor={`${id}-mode`}>{label} source</label>
        <Select
          virtual={false}
          id={`${id}-mode`}
          value={mode}
          onChange={(next) => {
            edit((a) => {
              if (next === "field") {
                a.delete("literal");
                if (!a.has("field")) a.set("field", "");
              } else if (next === "literal") {
                a.delete("field");
                if (!a.has("literal")) a.set("literal", "");
              } else {
                a.delete("field");
                a.delete("literal");
              }
            });
          }}
          options={[
            { value: "unset", label: "(unset)" },
            { value: "field", label: "source field" },
            { value: "literal", label: "literal" },
          ]}
        />
      </div>

      {mode === "field" ? (
        <TextField
          label={`${label} field`}
          value={fieldText}
          commit={(text) => {
            edit((a) => {
              a.set("field", text);
            });
            return text;
          }}
          diagnostics={diagnostics}
          path={`${diagPath}.field`}
        />
      ) : null}

      {mode === "literal" && literalKind === "scalar" ? (
        <SelectField
          label={`${label} literal type`}
          value={literalType}
          options={["string", "json"]}
          commit={(kind) => {
            const stored =
              kind === "json" ? parseLiteral(literalText) : literalText;
            lastKindSync.current =
              typeof stored === "string" ? "string" : "json";
            setLiteralType(kind === "json" ? "json" : "string");
            edit((a) => {
              a.set("literal", stored);
            });
            return kind;
          }}
          diagnostics={diagnostics}
          path={`${diagPath}.literal`}
        />
      ) : null}

      {mode === "literal" ? (
        <TextField
          label={`${label} literal`}
          value={literalText}
          commit={(text) => {
            const stored =
              literalKind === "string" || literalType === "string"
                ? text
                : parseLiteral(text);
            lastKindSync.current =
              typeof stored === "string" ? "string" : "json";
            edit((a) => {
              a.set("literal", stored);
            });
            return text;
          }}
          diagnostics={diagnostics}
          path={`${diagPath}.literal`}
        />
      ) : null}

      {casts !== null ? (
        <SelectField
          label={`${label} cast (as)`}
          value={castText}
          options={casts}
          commit={(text) => {
            edit((a) => {
              if (text === "") a.delete("as");
              else a.set("as", text);
            });
            return text;
          }}
          diagnostics={diagnostics}
          path={`${diagPath}.as`}
        />
      ) : null}

      <FieldDiagnostics diagnostics={diagnostics} path={diagPath} deep />
    </div>
  );
}

function EntityBindingCard({
  index,
  base,
  item,
  diagnostics,
  entityOptions,
  propertyOptions,
  vocabulary,
  editItem,
  onRemove,
}: {
  index: number;
  /** Diagnostic path prefix, e.g. `mapping.entities[0]` or
   *  `mapping.sources[1].entities[0]`. */
  base: string;
  /** The seq item node — commits locate it by identity. */
  item: unknown;
  diagnostics: Diagnostic[];
  entityOptions: { id: string; options: string[] };
  propertyOptions: (entity: string) => string[];
  vocabulary: ContractVocabulary | null;
  editItem: (item: unknown, fn: (m: YAMLMap, d: Document) => void) => void;
  onRemove: () => void;
}) {
  const [newProp, setNewProp] = useState("");
  const propListId = useId();
  const map = isMap(item) ? item : null;
  const entityName = map ? scalarText(memberValue(map, "entity")) : "";
  const props = map ? mapAt(map, ["properties"]) : null;
  const suggestions = propertyOptions(entityName);

  return (
    <fieldset className="form-card">
      <legend>Entity binding {index + 1}</legend>
      <FieldDiagnostics diagnostics={diagnostics} path={base} deep={false} />
      <TextField
        label="Entity"
        value={entityName}
        commit={(text) => {
          editItem(item, (m) => {
            commitText(m, ["entity"], text);
          });
          return text;
        }}
        diagnostics={diagnostics}
        path={`${base}.entity`}
        datalist={entityOptions}
      />
      <AssignmentFields
        label="Entity id"
        node={map?.getIn(["id"], true)}
        diagPath={`${base}.id`}
        diagnostics={diagnostics}
        casts={null}
        literalKind="string"
        edit={(fn) =>
          editItem(item, (m, d) => fn(ensureAssignment(m, ["id"], d), d))
        }
      />

      <div className="assignment-list">
        <p className="hint">Property assignments</p>
        {(props?.items ?? []).map((pair, j) => {
          const name = keyText(pair.key);
          return (
            <div className="assignment-row" key={j}>
              <AssignmentFields
                label={`Property ${name}`}
                node={pair.value}
                diagPath={`${base}.properties.${name}`}
                diagnostics={diagnostics}
                casts={vocabulary?.castTypes ?? []}
                literalKind="scalar"
                edit={(fn) =>
                  editItem(item, (m, d) => {
                    const live = mapAt(m, ["properties"]);
                    const idx = live ? live.items.indexOf(pair) : -1;
                    if (!live || idx < 0) return;
                    if (!isMap(live.items[idx].value)) {
                      live.items[idx].value = d.createNode({});
                    }
                    fn(live.items[idx].value as YAMLMap, d);
                  })
                }
              />
              <Button
                onClick={() =>
                  editItem(item, (m) => {
                    const live = mapAt(m, ["properties"]);
                    const idx = live ? live.items.indexOf(pair) : -1;
                    if (live && idx >= 0) live.items.splice(idx, 1);
                  })
                }
              >
                Remove property {name}
              </Button>
            </div>
          );
        })}
        <div className="form-field">
          <label htmlFor={propListId}>New property name</label>
          <Input
            id={propListId}
            value={newProp}
            list={suggestions.length ? `${propListId}-options` : undefined}
            onChange={(e) => setNewProp(e.target.value)}
          />
          {suggestions.length ? (
            <datalist id={`${propListId}-options`}>
              {suggestions.map((o) => (
                <option key={o} value={o} />
              ))}
            </datalist>
          ) : null}
        </div>
        <Button
          disabled={newProp.trim() === ""}
          onClick={() => {
            const name = newProp.trim();
            editItem(item, (m, d) => {
              const live =
                mapAt(m, ["properties"]) ??
                (() => {
                  const created = d.createNode({});
                  m.set("properties", created);
                  return created;
                })();
              if (!live.has(name)) {
                live.set(name, d.createNode({ field: "" }));
              }
            });
            setNewProp("");
          }}
        >
          Add property assignment
        </Button>
      </div>

      <Button onClick={onRemove}>Remove entity binding</Button>
    </fieldset>
  );
}

function EventBindingCard({
  index,
  base,
  item,
  diagnostics,
  eventOptions,
  entityOptions,
  vocabulary,
  timeFields,
  editItem,
  onRemove,
}: {
  index: number;
  /** Diagnostic path prefix, e.g. `mapping.events[0]` or
   *  `mapping.sources[1].events[0]`. */
  base: string;
  item: unknown;
  diagnostics: Diagnostic[];
  eventOptions: { id: string; options: string[] };
  entityOptions: { id: string; options: string[] };
  vocabulary: ContractVocabulary | null;
  timeFields: { occurrence: string; observation: string };
  editItem: (item: unknown, fn: (m: YAMLMap, d: Document) => void) => void;
  onRemove: () => void;
}) {
  const map = isMap(item) ? item : null;
  const whenNode = map?.getIn(["when"], true);
  const observedNode = map?.getIn([timeFields.observation], true);

  return (
    <fieldset className="form-card">
      <legend>Event binding {index + 1}</legend>
      <FieldDiagnostics diagnostics={diagnostics} path={base} deep={false} />
      <TextField
        label="Event"
        value={map ? scalarText(memberValue(map, "event")) : ""}
        commit={(text) => {
          editItem(item, (m) => {
            commitText(m, ["event"], text);
          });
          return text;
        }}
        diagnostics={diagnostics}
        path={`${base}.event`}
        datalist={eventOptions}
      />
      <TextField
        label="Subject entity"
        value={map ? scalarText(map.getIn(["subject", "entity"], true)) : ""}
        commit={(text) => {
          editItem(item, (m, d) => {
            commitText(ensureAssignment(m, ["subject"], d), ["entity"], text);
          });
          return text;
        }}
        diagnostics={diagnostics}
        path={`${base}.subject.entity`}
        datalist={entityOptions}
      />
      <AssignmentFields
        label="Subject id"
        node={map?.getIn(["subject", "id"], true)}
        diagPath={`${base}.subject.id`}
        diagnostics={diagnostics}
        casts={null}
        literalKind="string"
        edit={(fn) =>
          editItem(item, (m, d) =>
            fn(ensureAssignment(m, ["subject", "id"], d), d),
          )
        }
      />

      <FormSection
        title="Condition (when)"
        path={`${base}.when`}
        diagnostics={diagnostics}
      >
        {whenNode === undefined ? (
          <Button
            onClick={() =>
              editItem(item, (m, d) => {
                m.set("when", d.createNode({ op: "eq", field: "", value: "" }));
              })
            }
          >
            Add condition
          </Button>
        ) : (
          <ConditionEditor
            node={whenNode}
            diagPath={`${base}.when`}
            diagnostics={diagnostics}
            vocabulary={vocabulary}
            depth={0}
            mutate={(fn) =>
              editItem(item, (m, d) => fn(ensureAssignment(m, ["when"], d), d))
            }
            replace={(value) =>
              editItem(item, (m, d) => {
                if (value === null) m.delete("when");
                else m.set("when", d.createNode(value));
              })
            }
          />
        )}
      </FormSection>

      <AssignmentFields
        label={`${timeFields.occurrence} (occurrence)`}
        node={map?.getIn([timeFields.occurrence], true)}
        diagPath={`${base}.${timeFields.occurrence}`}
        diagnostics={diagnostics}
        casts={vocabulary?.timeCasts ?? []}
        literalKind="string"
        edit={(fn) =>
          editItem(item, (m, d) =>
            fn(ensureAssignment(m, [timeFields.occurrence], d), d),
          )
        }
      />

      {observedNode === undefined ? (
        <Button
          onClick={() =>
            editItem(item, (m, d) => {
              m.set(
                timeFields.observation,
                d.createNode({ field: "", as: "datetime" }),
              );
            })
          }
        >
          Add {timeFields.observation}
        </Button>
      ) : (
        <div className="assignment-row">
          <AssignmentFields
            label={`${timeFields.observation} (observation)`}
            node={observedNode}
            diagPath={`${base}.${timeFields.observation}`}
            diagnostics={diagnostics}
            casts={vocabulary?.timeCasts ?? []}
            literalKind="string"
            edit={(fn) =>
              editItem(item, (m, d) =>
                fn(ensureAssignment(m, [timeFields.observation], d), d),
              )
            }
          />
          <Button
            onClick={() =>
              editItem(item, (m) => {
                m.delete(timeFields.observation);
              })
            }
          >
            Remove {timeFields.observation}
          </Button>
        </div>
      )}

      <Button onClick={onRemove}>Remove event binding</Button>
    </fieldset>
  );
}

/** Shared props the binding sections need from `MappingForm`. */
interface BindingContext {
  diagnostics: Diagnostic[];
  entityOptions: { id: string; options: string[] };
  eventOptions: { id: string; options: string[] };
  domainDraft: string | null;
  domainFallback: DomainDefinition | null;
  vocabulary: ContractVocabulary | null;
  timeFields: { occurrence: string; observation: string };
  update: <T>(fn: (d: Document) => T) => T | undefined;
  /** Locate a seq item by node identity within the seq at `seqPath`, then
   *  mutate it — a remove queued in the same render pass cannot shift
   *  this write. */
  editItem: (
    seqPath: readonly (string | number)[],
  ) => (item: unknown, fn: (m: YAMLMap, d: Document) => void) => void;
  removeItem: (
    seqPath: readonly (string | number)[],
  ) => (item: unknown) => void;
}

/**
 * One source's binding lists — either the mapping's top-level
 * entities/events (single-source form) or a `sources[]` entry's.
 * `containerPath` is the doc path to the map holding the binding keys;
 * `diagBase` is the same location as a core diagnostic path.
 */
function BindingsSections({
  doc,
  containerPath,
  diagBase,
  ctx,
}: {
  doc: Document;
  containerPath: readonly (string | number)[];
  diagBase: string;
  ctx: BindingContext;
}) {
  const {
    diagnostics,
    entityOptions,
    eventOptions,
    domainDraft,
    domainFallback,
    vocabulary,
    timeFields,
    update,
    editItem,
    removeItem,
  } = ctx;
  const entitiesPath = [...containerPath, "entities"];
  const eventsPath = [...containerPath, "events"];
  const entities = seqAt(doc, entitiesPath);
  const events = seqAt(doc, eventsPath);
  return (
    <>
      <FormSection
        title="Entity bindings"
        path={`${diagBase}.entities`}
        diagnostics={diagnostics}
        action={
          <Button
            onClick={() =>
              update((d) => {
                ensureSeq(d, entitiesPath).add(
                  d.createNode({ entity: "", id: { field: "" } }),
                );
              })
            }
          >
            Add entity binding
          </Button>
        }
      >
        {entities === null || entities.items.length === 0 ? (
          <p className="hint">No entity bindings in the draft.</p>
        ) : (
          entities.items.map((item, i) => (
            <EntityBindingCard
              key={i}
              index={i}
              base={`${diagBase}.entities[${i}]`}
              item={item}
              diagnostics={diagnostics}
              entityOptions={entityOptions}
              propertyOptions={(entity) =>
                domainEntityProperties(domainDraft, domainFallback, entity)
              }
              vocabulary={vocabulary}
              editItem={editItem(entitiesPath)}
              onRemove={() => removeItem(entitiesPath)(item)}
            />
          ))
        )}
      </FormSection>

      <FormSection
        title="Event bindings"
        path={`${diagBase}.events`}
        diagnostics={diagnostics}
        action={
          <Button
            onClick={() =>
              update((d) => {
                ensureSeq(d, eventsPath).add(
                  d.createNode({
                    event: "",
                    subject: { entity: "", id: { field: "" } },
                    [timeFields.occurrence]: { field: "", as: "datetime" },
                  }),
                );
              })
            }
          >
            Add event binding
          </Button>
        }
      >
        {events === null || events.items.length === 0 ? (
          <p className="hint">No event bindings in the draft.</p>
        ) : (
          events.items.map((item, i) => (
            <EventBindingCard
              key={i}
              index={i}
              base={`${diagBase}.events[${i}]`}
              item={item}
              diagnostics={diagnostics}
              eventOptions={eventOptions}
              entityOptions={entityOptions}
              vocabulary={vocabulary}
              timeFields={timeFields}
              editItem={editItem(eventsPath)}
              onRemove={() => removeItem(eventsPath)(item)}
            />
          ))
        )}
      </FormSection>
    </>
  );
}

/** One `mapping.sources[i]` entry: its name/format plus scoped bindings. */
function SourceScope({
  index,
  item,
  ctx,
}: {
  index: number;
  item: unknown;
  ctx: BindingContext & {
    value: (path: readonly (string | number)[]) => string;
    textCommit: (
      path: readonly (string | number)[],
    ) => (text: string) => string;
    doc: Document;
  };
}) {
  const { diagnostics, vocabulary, value, textCommit, doc, removeItem } = ctx;
  const path = [ROOT, "sources", index];
  const diagBase = `mapping.sources[${index}]`;
  return (
    <fieldset className="form-card">
      <legend>Source {index + 1}</legend>
      <FieldDiagnostics
        diagnostics={diagnostics}
        path={diagBase}
        deep={false}
      />
      <TextField
        label="Source name"
        value={value([...path, "name"])}
        commit={textCommit([...path, "name"])}
        diagnostics={diagnostics}
        path={`${diagBase}.name`}
      />
      <SelectField
        label="Source format"
        value={value([...path, "format"])}
        options={vocabulary?.sourceFormats ?? null}
        commit={textCommit([...path, "format"])}
        diagnostics={diagnostics}
        path={`${diagBase}.format`}
      />
      <BindingsSections
        doc={doc}
        containerPath={path}
        diagBase={diagBase}
        ctx={ctx}
      />
      <Button onClick={() => removeItem([ROOT, "sources"])(item)}>
        Remove source
      </Button>
    </fieldset>
  );
}

export function MappingForm() {
  const { state } = useWorkspace();
  const { parsed, update } = useDraftDoc(FILE);
  const { contract } = useContract();
  const vocabulary = contractVocabulary(contract);
  const diagnostics = useFileDiagnostics(FILE);
  const refs = useDomainReferences();
  const domainDraft = state.drafts["domain.yaml"] ?? null;
  const domainFallback: DomainDefinition | null =
    state.workspace?.definitions?.domain ?? null;
  const timeFields = domainEventTimeFields(domainDraft, domainFallback);

  if (parsed.status !== "ready") {
    return (
      <section
        className="definition-form"
        aria-labelledby="mapping-form-heading"
      >
        <h2 id="mapping-form-heading">Edit mapping</h2>
        <DraftDocGate file={FILE} kind="mapping" parsed={parsed} />
      </section>
    );
  }
  const doc = parsed.doc;

  const value = (path: readonly (string | number)[]) =>
    scalarText(doc.getIn(path));
  const textCommit =
    (path: readonly (string | number)[]) =>
    (text: string): string =>
      update((d) => commitText(d, path, text)) ?? text;

  const entityOptions = {
    id: "mapping-domain-entities",
    options: refs.entities,
  };
  const eventOptions = { id: "mapping-domain-events", options: refs.events };

  const editItem: BindingContext["editItem"] = (seqPath) => (item, fn) =>
    update((d) => {
      const seq = seqAt(d, seqPath);
      const idx = seq ? seq.items.indexOf(item) : -1;
      if (!seq || idx < 0) return;
      if (!isMap(seq.items[idx])) seq.items[idx] = d.createNode({});
      fn(seq.items[idx] as YAMLMap, d);
    });

  const removeItem: BindingContext["removeItem"] = (seqPath) => (item) =>
    update((d) => {
      const seq = seqAt(d, seqPath);
      const idx = seq ? seq.items.indexOf(item) : -1;
      if (seq && idx >= 0) seq.items.splice(idx, 1);
    });

  const ctx: BindingContext = {
    diagnostics,
    entityOptions,
    eventOptions,
    domainDraft,
    domainFallback,
    vocabulary,
    timeFields,
    update,
    editItem,
    removeItem,
  };

  // A draft declaring `sources:` renders one section per source; the
  // single-source `source:` form keeps its original shape.
  const sourcesSeq = seqAt(doc, [ROOT, "sources"]);

  return (
    <section className="definition-form" aria-labelledby="mapping-form-heading">
      <h2 id="mapping-form-heading">Edit mapping</h2>
      <p className="hint">
        Edits write the draft below; “Validate draft” and “Run preview” apply
        the shared core to the draft bundle.
      </p>

      <TextField
        label="Name"
        value={value([ROOT, "name"])}
        commit={textCommit([ROOT, "name"])}
        diagnostics={diagnostics}
        path="mapping.name"
      />
      <TextField
        label="Description"
        value={value([ROOT, "description"])}
        commit={textCommit([ROOT, "description"])}
        diagnostics={diagnostics}
        path="mapping.description"
      />

      {sourcesSeq === null ? (
        <>
          <FormSection
            title="Source"
            path="mapping.source"
            diagnostics={diagnostics}
            action={
              <Button
                onClick={() =>
                  update((d) => {
                    const m = mapAt(d, [ROOT]);
                    if (!m) return;
                    const source = m.get("source", true);
                    const entities = m.get("entities", true);
                    const events = m.get("events", true);
                    const entry = d.createNode({}) as YAMLMap;
                    // Move the declared name/format pairs (and their
                    // comments) into the new source entry.
                    if (isMap(source)) entry.items = [...source.items];
                    entry.set(
                      "entities",
                      isSeq(entities) ? entities : d.createNode([]),
                    );
                    entry.set(
                      "events",
                      isSeq(events) ? events : d.createNode([]),
                    );
                    const seq = d.createNode([entry]);
                    m.set("sources", seq);
                    m.delete("source");
                    m.delete("entities");
                    m.delete("events");
                  })
                }
              >
                Convert to sources list
              </Button>
            }
          >
            <TextField
              label="Source name"
              value={value([ROOT, "source", "name"])}
              commit={textCommit([ROOT, "source", "name"])}
              diagnostics={diagnostics}
              path="mapping.source.name"
            />
            <SelectField
              label="Source format"
              value={value([ROOT, "source", "format"])}
              options={vocabulary?.sourceFormats ?? null}
              commit={textCommit([ROOT, "source", "format"])}
              diagnostics={diagnostics}
              path="mapping.source.format"
            />
          </FormSection>

          <BindingsSections
            doc={doc}
            containerPath={[ROOT]}
            diagBase="mapping"
            ctx={ctx}
          />
        </>
      ) : (
        <FormSection
          title="Sources"
          path="mapping.sources"
          diagnostics={diagnostics}
          action={
            <Button
              onClick={() =>
                update((d) => {
                  ensureSeq(d, [ROOT, "sources"]).add(
                    d.createNode({
                      name: "",
                      format: "",
                      entities: [],
                      events: [],
                    }),
                  );
                })
              }
            >
              Add source
            </Button>
          }
        >
          {sourcesSeq.items.length === 0 ? (
            <p className="hint">No sources in the draft.</p>
          ) : (
            sourcesSeq.items.map((item, i) => (
              <SourceScope
                key={i}
                index={i}
                item={item}
                ctx={{ ...ctx, value, textCommit, doc }}
              />
            ))
          )}
        </FormSection>
      )}
    </section>
  );
}
