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

import { useId, useState } from "react";
import { isMap, isSeq } from "yaml";
import type { Document, YAMLMap } from "yaml";

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
        This condition is nested too deeply for the form — edit it in the
        raw YAML editor below.
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
        <TextField
          label="Values (comma-separated)"
          value={(() => {
            const vs = cond?.get("values", true);
            return isSeq(vs)
              ? vs.items.map((v) => scalarText(v)).join(", ")
              : nodeText(vs);
          })()}
          commit={(text) => {
            mutate((m, d) => {
              m.set(
                "values",
                d.createNode(
                  text
                    .split(",")
                    .map((v) => v.trim())
                    .filter((v) => v !== "")
                    .map(parseLiteral),
                ),
              );
            });
            return text;
          }}
          diagnostics={diagnostics}
          path={`${diagPath}.values`}
        />
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
          <button
            type="button"
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
          </button>
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

      <button type="button" onClick={() => replace(null)}>
        Remove condition
      </button>
      <FieldDiagnostics diagnostics={diagnostics} path={diagPath} deep />
    </div>
  );
}

/**
 * One `{field|literal, as}` assignment. `casts` is null where casts are
 * forbidden (ids/subjects), a vocabulary list elsewhere — the event-time
 * positions get the restricted `time_casts` list.
 */
function AssignmentFields({
  label,
  node,
  diagPath,
  diagnostics,
  casts,
  edit,
}: {
  label: string;
  /** Render-time assignment node (YAMLMap or something malformed). */
  node: unknown;
  diagPath: string;
  diagnostics: Diagnostic[];
  casts: string[] | null;
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
  const castText = assign ? scalarText(assign.get("as", true)) : "";
  const id = useId();

  return (
    <div className="assignment">
      <div className="form-field">
        <label htmlFor={`${id}-mode`}>{label} source</label>
        <select
          id={`${id}-mode`}
          value={mode}
          onChange={(e) => {
            const next = e.target.value;
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
        >
          <option value="unset">(unset)</option>
          <option value="field">source field</option>
          <option value="literal">literal</option>
        </select>
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

      {mode === "literal" ? (
        <TextField
          label={`${label} literal`}
          value={literalText}
          commit={(text) => {
            edit((a) => {
              a.set("literal", parseLiteral(text));
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
  item,
  diagnostics,
  entityOptions,
  propertyOptions,
  vocabulary,
  editItem,
  onRemove,
}: {
  index: number;
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
  const base = `mapping.entities[${index}]`;
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
                casts={vocabulary?.castTypes ?? null}
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
              <button
                type="button"
                onClick={() =>
                  editItem(item, (m) => {
                    const live = mapAt(m, ["properties"]);
                    const idx = live ? live.items.indexOf(pair) : -1;
                    if (live && idx >= 0) live.items.splice(idx, 1);
                  })
                }
              >
                Remove property {name}
              </button>
            </div>
          );
        })}
        <div className="form-field">
          <label htmlFor={propListId}>New property name</label>
          <input
            id={propListId}
            type="text"
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
        <button
          type="button"
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
        </button>
      </div>

      <button type="button" onClick={onRemove}>
        Remove entity binding
      </button>
    </fieldset>
  );
}

function EventBindingCard({
  index,
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
  item: unknown;
  diagnostics: Diagnostic[];
  eventOptions: { id: string; options: string[] };
  entityOptions: { id: string; options: string[] };
  vocabulary: ContractVocabulary | null;
  timeFields: { occurrence: string; observation: string };
  editItem: (item: unknown, fn: (m: YAMLMap, d: Document) => void) => void;
  onRemove: () => void;
}) {
  const base = `mapping.events[${index}]`;
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
        value={
          map ? scalarText(map.getIn(["subject", "entity"], true)) : ""
        }
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
          <button
            type="button"
            onClick={() =>
              editItem(item, (m, d) => {
                m.set("when", d.createNode({ op: "eq", field: "", value: "" }));
              })
            }
          >
            Add condition
          </button>
        ) : (
          <ConditionEditor
            node={whenNode}
            diagPath={`${base}.when`}
            diagnostics={diagnostics}
            vocabulary={vocabulary}
            depth={0}
            mutate={(fn) =>
              editItem(item, (m, d) =>
                fn(ensureAssignment(m, ["when"], d), d),
              )
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
        casts={vocabulary?.timeCasts ?? ["date", "datetime"]}
        edit={(fn) =>
          editItem(item, (m, d) =>
            fn(ensureAssignment(m, [timeFields.occurrence], d), d),
          )
        }
      />

      {observedNode === undefined ? (
        <button
          type="button"
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
        </button>
      ) : (
        <div className="assignment-row">
          <AssignmentFields
            label={`${timeFields.observation} (observation)`}
            node={observedNode}
            diagPath={`${base}.${timeFields.observation}`}
            diagnostics={diagnostics}
            casts={vocabulary?.timeCasts ?? ["date", "datetime"]}
            edit={(fn) =>
              editItem(item, (m, d) =>
                fn(ensureAssignment(m, [timeFields.observation], d), d),
              )
            }
          />
          <button
            type="button"
            onClick={() =>
              editItem(item, (m) => {
                m.delete(timeFields.observation);
              })
            }
          >
            Remove {timeFields.observation}
          </button>
        </div>
      )}

      <button type="button" onClick={onRemove}>
        Remove event binding
      </button>
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
      <section className="definition-form" aria-labelledby="mapping-form-heading">
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

  const entities = seqAt(doc, [ROOT, "entities"]);
  const events = seqAt(doc, [ROOT, "events"]);
  const entityOptions = { id: "mapping-domain-entities", options: refs.entities };
  const eventOptions = { id: "mapping-domain-events", options: refs.events };

  /** Locate a bindings seq item by node identity, then mutate it — a
   *  remove queued in the same render pass cannot shift this write. */
  const editItem =
    (seqKey: "entities" | "events") =>
    (item: unknown, fn: (m: YAMLMap, d: Document) => void) =>
      update((d) => {
        const seq = seqAt(d, [ROOT, seqKey]);
        const idx = seq ? seq.items.indexOf(item) : -1;
        if (!seq || idx < 0) return;
        if (!isMap(seq.items[idx])) seq.items[idx] = d.createNode({});
        fn(seq.items[idx] as YAMLMap, d);
      });

  const removeItem =
    (seqKey: "entities" | "events") =>
    (item: unknown) =>
      update((d) => {
        const seq = seqAt(d, [ROOT, seqKey]);
        const idx = seq ? seq.items.indexOf(item) : -1;
        if (seq && idx >= 0) seq.items.splice(idx, 1);
      });

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

      <FormSection
        title="Source"
        path="mapping.source"
        diagnostics={diagnostics}
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

      <FormSection
        title="Entity bindings"
        path="mapping.entities"
        diagnostics={diagnostics}
        action={
          <button
            type="button"
            onClick={() =>
              update((d) => {
                ensureSeq(d, [ROOT, "entities"]).add(
                  d.createNode({ entity: "", id: { field: "" } }),
                );
              })
            }
          >
            Add entity binding
          </button>
        }
      >
        {entities === null || entities.items.length === 0 ? (
          <p className="hint">No entity bindings in the draft.</p>
        ) : (
          entities.items.map((item, i) => (
            <EntityBindingCard
              key={i}
              index={i}
              item={item}
              diagnostics={diagnostics}
              entityOptions={entityOptions}
              propertyOptions={(entity) =>
                domainEntityProperties(domainDraft, domainFallback, entity)
              }
              vocabulary={vocabulary}
              editItem={editItem("entities")}
              onRemove={() => removeItem("entities")(item)}
            />
          ))
        )}
      </FormSection>

      <FormSection
        title="Event bindings"
        path="mapping.events"
        diagnostics={diagnostics}
        action={
          <button
            type="button"
            onClick={() =>
              update((d) => {
                ensureSeq(d, [ROOT, "events"]).add(
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
          </button>
        }
      >
        {events === null || events.items.length === 0 ? (
          <p className="hint">No event bindings in the draft.</p>
        ) : (
          events.items.map((item, i) => (
            <EventBindingCard
              key={i}
              index={i}
              item={item}
              diagnostics={diagnostics}
              eventOptions={eventOptions}
              entityOptions={entityOptions}
              vocabulary={vocabulary}
              timeFields={timeFields}
              editItem={editItem("events")}
              onRemove={() => removeItem("events")(item)}
            />
          ))
        )}
      </FormSection>
    </section>
  );
}
