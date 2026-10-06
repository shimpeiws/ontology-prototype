/**
 * Structured editor for `agent.yaml` (#18): projection input, goal,
 * deliverables, and permitted action schemas.
 *
 * The definition carries no provider, model, or credential fields by
 * contract — this form exposes none, and nothing it writes leaves the
 * draft buffer. Vocabularies (deliverables, property types) come from
 * `GET /api/v1/contract`; projection/entity suggestions come from the
 * domain draft. The core stays authoritative for every rule.
 */

import { isMap } from "yaml";
import type { YAMLMap } from "yaml";

import { contractVocabulary, useContract } from "../lib/contract";
import {
  commitBool,
  commitText,
  ensureMap,
  ensureSeq,
  keyText,
  mapAt,
  memberValue,
  renamePairKey,
  scalarText,
  seqAt,
} from "../lib/draftDoc";
import {
  CheckField,
  DraftDocGate,
  FieldDiagnostics,
  FormSection,
  SelectField,
  TextField,
  useDomainReferences,
  useDraftDoc,
  useFileDiagnostics,
} from "./FormControls";

const FILE = "agent.yaml" as const;
const ROOT = "agent";
const ACTIONS = [ROOT, "permitted_actions"] as const;

/** First `base` not in `taken`, else `base_2`, `base_3`, … */
function uniqueName(taken: readonly string[], base: string): string {
  const set = new Set(taken);
  if (!set.has(base)) return base;
  for (let i = 2; ; i += 1) {
    if (!set.has(`${base}_${i}`)) return `${base}_${i}`;
  }
}

export function AgentForm() {
  const { parsed, update } = useDraftDoc(FILE);
  const { contract } = useContract();
  const vocabulary = contractVocabulary(contract);
  const diagnostics = useFileDiagnostics(FILE);
  const refs = useDomainReferences();

  if (parsed.status !== "ready") {
    return (
      <section className="definition-form" aria-labelledby="agent-form-heading">
        <h2 id="agent-form-heading">Edit agent task</h2>
        <DraftDocGate file={FILE} kind="agent" parsed={parsed} />
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

  const deliverables = seqAt(doc, [ROOT, "task", "deliverables"]);
  const deliverableValues =
    deliverables?.items.map((item) => scalarText(item)) ?? [];
  const knownDeliverables = vocabulary?.deliverables ?? null;
  const actions = mapAt(doc, ACTIONS);
  const entityList = { id: "agent-domain-entities", options: refs.entities };

  function toggleDeliverable(name: string, checked: boolean) {
    update((d) => {
      const seq = ensureSeq(d, [ROOT, "task", "deliverables"]);
      if (checked) {
        const present = seq.items.some(
          (item) => scalarText(item) === name,
        );
        if (!present) seq.add(d.createNode(name));
      } else {
        // Delete matching items from the end so earlier indices stay valid.
        for (let i = seq.items.length - 1; i >= 0; i -= 1) {
          if (scalarText(seq.items[i]) === name) seq.items.splice(i, 1);
        }
      }
    });
  }

  return (
    <section className="definition-form" aria-labelledby="agent-form-heading">
      <h2 id="agent-form-heading">Edit agent task</h2>
      <p className="hint">
        Provider and model credentials are not part of this definition — the
        editor cannot write them, and nothing here is stored in the browser.
      </p>

      <TextField
        label="Name"
        value={value([ROOT, "name"])}
        commit={textCommit([ROOT, "name"])}
        diagnostics={diagnostics}
        path="agent.name"
      />
      <TextField
        label="Description"
        value={value([ROOT, "description"])}
        commit={textCommit([ROOT, "description"])}
        diagnostics={diagnostics}
        path="agent.description"
      />
      <TextField
        label="Projection"
        value={value([ROOT, "projection"])}
        commit={textCommit([ROOT, "projection"])}
        diagnostics={diagnostics}
        path="agent.projection"
        datalist={{
          id: "agent-domain-projections",
          options: refs.projections,
        }}
      />

      <FormSection
        title="Task"
        path="agent.task"
        diagnostics={diagnostics}
      >
        <TextField
          label="Goal"
          value={value([ROOT, "task", "goal"])}
          commit={textCommit([ROOT, "task", "goal"])}
          diagnostics={diagnostics}
          path="agent.task.goal"
        />
        <fieldset className="form-field">
          <legend>Deliverables</legend>
          {knownDeliverables === null ? (
            <>
              <p className="hint">
                Contract vocabulary unavailable — deliverables in the draft:
              </p>
              <ul>
                {deliverableValues.map((v) => (
                  <li key={v}>
                    <code>{v}</code>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <>
              {knownDeliverables.map((name) => (
                <CheckField
                  key={name}
                  label={name}
                  checked={deliverableValues.includes(name)}
                  onChange={(checked) => toggleDeliverable(name, checked)}
                  diagnostics={diagnostics}
                  path="agent.task.deliverables"
                />
              ))}
              {(deliverables?.items ?? [])
                .map((item, i) => ({ item, i }))
                .filter(({ item }) => {
                  const v = scalarText(item);
                  return v !== "" && !knownDeliverables.includes(v);
                })
                .map(({ item, i }) => (
                  <div key={`${scalarText(item)}-${i}`} className="form-field">
                    <code>{scalarText(item)}</code>{" "}
                    <span className="hint">unsupported deliverable</span>{" "}
                    <button
                      type="button"
                      onClick={() =>
                        update((d) => {
                          const seq = seqAt(d, [ROOT, "task", "deliverables"]);
                          const idx = seq ? seq.items.indexOf(item) : -1;
                          if (idx >= 0) seq?.items.splice(idx, 1);
                        })
                      }
                    >
                      Remove
                    </button>
                  </div>
                ))}
            </>
          )}
          <FieldDiagnostics
            diagnostics={diagnostics}
            path="agent.task.deliverables"
            deep
          />
        </fieldset>
      </FormSection>

      <FormSection
        title="Permitted actions"
        path="agent.permitted_actions"
        diagnostics={diagnostics}
        action={
          <button
            type="button"
            onClick={() =>
              update((d) => {
                const map = ensureMap(d, [...ACTIONS]);
                const name = uniqueName(
                  map.items.map((p) => keyText(p.key)),
                  "NewAction",
                );
                map.set(
                  name,
                  d.createNode({ description: "", params: {} }),
                );
              })
            }
          >
            Add action
          </button>
        }
      >
        {actions === null || actions.items.length === 0 ? (
          <p className="hint">No permitted actions in the draft.</p>
        ) : (
          actions.items.map((pair, i) => {
            const name = keyText(pair.key);
            const spec = isMap(pair.value) ? pair.value : null;
            const actionPath = `agent.permitted_actions.${name}`;
            const params = spec ? mapAt(spec, ["params"]) : null;

            // Ensure the action spec is a map so field writes never hit a
            // scalar node. The pair is located by node identity at commit
            // time so a remove in the same render cycle cannot shift this
            // write onto a sibling; returns null when the pair is gone.
            const specMap = (d: typeof doc): YAMLMap | null => {
              const m = mapAt(d, ACTIONS);
              const idx = m ? m.items.indexOf(pair) : -1;
              const p = idx >= 0 ? m?.items[idx] : undefined;
              if (!p) return null;
              if (!isMap(p.value)) p.value = d.createNode({});
              return p.value as YAMLMap;
            };

            return (
              <fieldset className="form-card" key={i}>
                <legend>Action {i + 1}</legend>
                <FieldDiagnostics
                  diagnostics={diagnostics}
                  path={actionPath}
                />
                <TextField
                  label={`Action ${i + 1} name`}
                  value={name}
                  commit={(text) =>
                    update((d) => {
                      const m = mapAt(d, ACTIONS);
                      if (m) renamePairKey(m, i, text);
                      return text;
                    }) ?? text
                  }
                  diagnostics={diagnostics}
                  path={actionPath}
                />
                <TextField
                  label={`Action ${i + 1} description`}
                  value={scalarText(memberValue(spec, "description"))}
                  commit={(text) =>
                    update((d) => {
                      const s = specMap(d);
                      return s ? commitText(s, ["description"], text) : text;
                    }) ?? text
                  }
                  diagnostics={diagnostics}
                  path={`${actionPath}.description`}
                />

                <fieldset className="form-field">
                  <legend>Params</legend>
                  <FieldDiagnostics
                    diagnostics={diagnostics}
                    path={`${actionPath}.params`}
                  />
                  {params === null || params.items.length === 0 ? (
                    <p className="hint">No params.</p>
                  ) : (
                    params.items.map((ppair, j) => {
                      const pname = keyText(ppair.key);
                      const pspec = isMap(ppair.value) ? ppair.value : null;
                      const paramPath = `${actionPath}.params.${pname}`;
                      const ptype = scalarText(memberValue(pspec, "type"));
                      const hasRef = memberValue(pspec, "ref") != null;

                      const pspecMap = (d: typeof doc): YAMLMap | null => {
                        const m = specMap(d);
                        const p = m ? mapAt(m, ["params"]) : null;
                        const idx = p ? p.items.indexOf(ppair) : -1;
                        const pp = idx >= 0 ? p?.items[idx] : undefined;
                        if (!pp) return null;
                        if (!isMap(pp.value)) pp.value = d.createNode({});
                        return pp.value as YAMLMap;
                      };
                      const paramCommit =
                        (key: string) =>
                        (text: string): string =>
                          update((d) => {
                            const s = pspecMap(d);
                            return s ? commitText(s, [key], text) : text;
                          }) ?? text;

                      return (
                        <fieldset className="form-card" key={j}>
                          <legend>Param {j + 1}</legend>
                          <FieldDiagnostics
                            diagnostics={diagnostics}
                            path={paramPath}
                          />
                          <TextField
                            label={`Action ${i + 1} param ${j + 1} name`}
                            value={pname}
                            commit={(text) =>
                              update((d) => {
                                const s = specMap(d);
                                const p = s ? mapAt(s, ["params"]) : null;
                                const idx = p ? p.items.indexOf(ppair) : -1;
                                if (p && idx >= 0) renamePairKey(p, idx, text);
                                return text;
                              }) ?? text
                            }
                            diagnostics={diagnostics}
                            path={paramPath}
                          />
                          <SelectField
                            label={`Action ${i + 1} param ${j + 1} type`}
                            value={ptype}
                            options={vocabulary?.propertyTypes ?? null}
                            commit={paramCommit("type")}
                            diagnostics={diagnostics}
                            path={`${paramPath}.type`}
                          />
                          {ptype === "ref" || hasRef ? (
                            <TextField
                              label={`Action ${i + 1} param ${j + 1} ref`}
                              value={scalarText(memberValue(pspec, "ref"))}
                              commit={paramCommit("ref")}
                              diagnostics={diagnostics}
                              path={`${paramPath}.ref`}
                              datalist={entityList}
                            />
                          ) : null}
                          <CheckField
                            label={`Action ${i + 1} param ${j + 1} required`}
                            checked={memberValue(pspec, "required") === true}
                            onChange={(checked) =>
                              update((d) => {
                                const s = pspecMap(d);
                                if (s) commitBool(s, ["required"], checked);
                              })
                            }
                            diagnostics={diagnostics}
                            path={`${paramPath}.required`}
                          />
                          <TextField
                            label={`Action ${i + 1} param ${j + 1} description`}
                            value={scalarText(memberValue(pspec, "description"))}
                            commit={paramCommit("description")}
                            diagnostics={diagnostics}
                            path={`${paramPath}.description`}
                          />
                          <button
                            type="button"
                            onClick={() =>
                              update((d) => {
                                const s = specMap(d);
                                const p = s ? mapAt(s, ["params"]) : null;
                                const idx = p ? p.items.indexOf(ppair) : -1;
                                if (idx >= 0) p?.items.splice(idx, 1);
                              })
                            }
                          >
                            Remove param
                          </button>
                        </fieldset>
                      );
                    })
                  )}
                  <button
                    type="button"
                    onClick={() =>
                      update((d) => {
                        const s = specMap(d);
                        if (!s) return;
                        let p = mapAt(s, ["params"]);
                        if (!p) {
                          p = d.createNode({}) as YAMLMap;
                          s.set("params", p);
                        }
                        const pname = uniqueName(
                          p.items.map((x) => keyText(x.key)),
                          "param",
                        );
                        p.set(pname, d.createNode({ type: "" }));
                      })
                    }
                  >
                    Add param
                  </button>
                </fieldset>

                <button
                  type="button"
                  onClick={() =>
                    update((d) => {
                      const m = mapAt(d, ACTIONS);
                      const idx = m ? m.items.indexOf(pair) : -1;
                      if (idx >= 0) m?.items.splice(idx, 1);
                    })
                  }
                >
                  Remove action
                </button>
              </fieldset>
            );
          })
        )}
      </FormSection>
    </section>
  );
}
