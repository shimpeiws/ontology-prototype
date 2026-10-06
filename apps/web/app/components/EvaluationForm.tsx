/**
 * Structured editor for `evaluation.yaml` (#18): interval events, metric,
 * aggregation, unit, baseline, target comparison, and guardrails.
 *
 * Options come from `GET /api/v1/contract` and event names from the domain
 * draft; the core stays authoritative for every rule (e.g. `lt` strictness,
 * `not_increase` taking no value). The form writes draft YAML only — see
 * FormControls for the sync contract.
 */

import { isMap } from "yaml";
import type { YAMLMap } from "yaml";

import { contractVocabulary, useContract } from "../lib/contract";
import {
  commitNumber,
  commitText,
  ensureSeq,
  memberValue,
  scalarText,
  seqAt,
} from "../lib/draftDoc";
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

const FILE = "evaluation.yaml" as const;
const ROOT = "evaluation";

export function EvaluationForm() {
  const { parsed, update } = useDraftDoc(FILE);
  const { contract } = useContract();
  const vocabulary = contractVocabulary(contract);
  const diagnostics = useFileDiagnostics(FILE);
  const refs = useDomainReferences();

  if (parsed.status !== "ready") {
    return (
      <section className="definition-form" aria-labelledby="eval-form-heading">
        <h2 id="eval-form-heading">Edit evaluation contract</h2>
        <DraftDocGate file={FILE} kind="evaluation" parsed={parsed} />
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
  const numberCommit =
    (path: readonly (string | number)[]) =>
    (text: string): string =>
      update((d) => commitNumber(d, path, text)) ?? text;

  const guardrails = seqAt(doc, [ROOT, "guardrails"]);
  const eventList = { id: "evaluation-domain-events", options: refs.events };

  return (
    <section className="definition-form" aria-labelledby="eval-form-heading">
      <h2 id="eval-form-heading">Edit evaluation contract</h2>
      <p className="hint">
        Edits write the draft below; “Validate draft” runs the shared core
        checks for references, comparators, and units.
      </p>

      <TextField
        label="Name"
        value={value([ROOT, "name"])}
        commit={textCommit([ROOT, "name"])}
        diagnostics={diagnostics}
        path="evaluation.name"
      />
      <TextField
        label="Description"
        value={value([ROOT, "description"])}
        commit={textCommit([ROOT, "description"])}
        diagnostics={diagnostics}
        path="evaluation.description"
      />

      <FormSection
        title="Interval"
        path="evaluation.interval"
        diagnostics={diagnostics}
      >
        <TextField
          label="Start event"
          value={value([ROOT, "interval", "start_event"])}
          commit={textCommit([ROOT, "interval", "start_event"])}
          diagnostics={diagnostics}
          path="evaluation.interval.start_event"
          datalist={eventList}
        />
        <TextField
          label="End event"
          value={value([ROOT, "interval", "end_event"])}
          commit={textCommit([ROOT, "interval", "end_event"])}
          diagnostics={diagnostics}
          path="evaluation.interval.end_event"
          datalist={eventList}
        />
      </FormSection>

      <SelectField
        label="Metric"
        value={value([ROOT, "metric"])}
        options={vocabulary?.metrics ?? null}
        commit={textCommit([ROOT, "metric"])}
        diagnostics={diagnostics}
        path="evaluation.metric"
      />
      <SelectField
        label="Aggregation"
        value={value([ROOT, "aggregation"])}
        options={vocabulary?.aggregations ?? null}
        commit={textCommit([ROOT, "aggregation"])}
        diagnostics={diagnostics}
        path="evaluation.aggregation"
      />
      <SelectField
        label="Unit"
        value={value([ROOT, "unit"])}
        options={vocabulary?.units ?? null}
        commit={textCommit([ROOT, "unit"])}
        diagnostics={diagnostics}
        path="evaluation.unit"
      />

      <FormSection
        title="Baseline"
        path="evaluation.baseline"
        diagnostics={diagnostics}
      >
        <TextField
          label="Baseline value"
          value={value([ROOT, "baseline", "value"])}
          commit={numberCommit([ROOT, "baseline", "value"])}
          diagnostics={diagnostics}
          path="evaluation.baseline.value"
          inputMode="decimal"
        />
        <TextField
          label="Baseline description"
          value={value([ROOT, "baseline", "description"])}
          commit={textCommit([ROOT, "baseline", "description"])}
          diagnostics={diagnostics}
          path="evaluation.baseline.description"
        />
      </FormSection>

      <FormSection
        title="Target"
        path="evaluation.target"
        diagnostics={diagnostics}
      >
        <SelectField
          label="Target comparator"
          value={value([ROOT, "target", "comparator"])}
          options={vocabulary?.comparators ?? null}
          commit={textCommit([ROOT, "target", "comparator"])}
          diagnostics={diagnostics}
          path="evaluation.target.comparator"
        />
        <TextField
          label="Target value"
          value={value([ROOT, "target", "value"])}
          commit={numberCommit([ROOT, "target", "value"])}
          diagnostics={diagnostics}
          path="evaluation.target.value"
          inputMode="decimal"
        />
        <TextField
          label="Target description"
          value={value([ROOT, "target", "description"])}
          commit={textCommit([ROOT, "target", "description"])}
          diagnostics={diagnostics}
          path="evaluation.target.description"
        />
      </FormSection>

      <FormSection
        title="Guardrails"
        path="evaluation.guardrails"
        diagnostics={diagnostics}
        action={
          <button
            type="button"
            onClick={() =>
              update((d) =>
                ensureSeq(d, [ROOT, "guardrails"]).add(
                  d.createNode({ name: "", metric: "", comparator: "" }),
                ),
              )
            }
          >
            Add guardrail
          </button>
        }
      >
        {guardrails === null || guardrails.items.length === 0 ? (
          <p className="hint">No guardrails in the draft.</p>
        ) : (
          guardrails.items.map((item, i) => {
            const itemPath = `evaluation.guardrails[${i}]`;
            // Locate the item by node identity at commit time so a remove in
            // the same render cycle cannot shift this write onto a sibling.
            const liveIndex = (d: typeof doc): number => {
              const seq = seqAt(d, [ROOT, "guardrails"]);
              return seq ? seq.items.indexOf(item) : -1;
            };
            // Writes go into the item map directly; a malformed (non-map)
            // item is replaced by an empty map so the field stays editable
            // instead of throwing on setIn against a scalar.
            const itemCommit =
              (key: string, numeric = false) =>
              (text: string): string =>
                update((d) => {
                  const seq = seqAt(d, [ROOT, "guardrails"]);
                  const idx = liveIndex(d);
                  if (!seq || idx < 0) return text;
                  if (!isMap(seq.items[idx])) {
                    seq.items[idx] = d.createNode({});
                  }
                  const host = seq.items[idx] as YAMLMap;
                  return numeric
                    ? commitNumber(host, [key], text)
                    : commitText(host, [key], text);
                }) ?? text;
            return (
              <fieldset className="form-card" key={i}>
                <legend>Guardrail {i + 1}</legend>
                <FieldDiagnostics diagnostics={diagnostics} path={itemPath} />
                <TextField
                  label={`Guardrail ${i + 1} name`}
                  value={scalarText(memberValue(item, "name"))}
                  commit={itemCommit("name")}
                  diagnostics={diagnostics}
                  path={`${itemPath}.name`}
                />
                <TextField
                  label={`Guardrail ${i + 1} metric`}
                  value={scalarText(memberValue(item, "metric"))}
                  commit={itemCommit("metric")}
                  diagnostics={diagnostics}
                  path={`${itemPath}.metric`}
                />
                <SelectField
                  label={`Guardrail ${i + 1} comparator`}
                  value={scalarText(memberValue(item, "comparator"))}
                  options={vocabulary?.guardrailComparators ?? null}
                  commit={itemCommit("comparator")}
                  diagnostics={diagnostics}
                  path={`${itemPath}.comparator`}
                />
                <TextField
                  label={`Guardrail ${i + 1} value`}
                  value={scalarText(memberValue(item, "value"))}
                  commit={itemCommit("value", true)}
                  diagnostics={diagnostics}
                  path={`${itemPath}.value`}
                  inputMode="decimal"
                />
                <TextField
                  label={`Guardrail ${i + 1} description`}
                  value={scalarText(memberValue(item, "description"))}
                  commit={itemCommit("description")}
                  diagnostics={diagnostics}
                  path={`${itemPath}.description`}
                />
                <button
                  type="button"
                  onClick={() =>
                    update((d) => {
                      const seq = seqAt(d, [ROOT, "guardrails"]);
                      const idx = seq ? seq.items.indexOf(item) : -1;
                      if (idx >= 0) seq?.items.splice(idx, 1);
                    })
                  }
                >
                  Remove guardrail
                </button>
              </fieldset>
            );
          })
        )}
      </FormSection>
    </section>
  );
}
