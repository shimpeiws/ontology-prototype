import { useWorkspace } from "../workspace/context";
import { DefinitionPage } from "../components/DefinitionPage";
import { EvaluationForm } from "../components/EvaluationForm";
import { EmptyState } from "../components/states";

export function meta() {
  return [{ title: "ontology — evaluation" }];
}

export default function EvaluationPage() {
  const { state } = useWorkspace();
  const evaluation = state.workspace?.definitions?.evaluation;
  if (!evaluation) {
    return (
      <DefinitionPage
        title="Evaluation"
        file="evaluation.yaml"
        form={<EvaluationForm />}
      />
    );
  }

  return (
    <DefinitionPage
      title="Evaluation"
      file="evaluation.yaml"
      form={<EvaluationForm />}
    >
      <p>
        <strong>{evaluation.name}</strong>
        {evaluation.description ? ` — ${evaluation.description}` : null}
      </p>

      <dl className="definition-facts">
        <dt>Interval</dt>
        <dd>
          <code>{evaluation.interval.start_event}</code> →{" "}
          <code>{evaluation.interval.end_event}</code>
        </dd>
        <dt>Metric</dt>
        <dd>
          <code>{evaluation.metric}</code> ({evaluation.aggregation},{" "}
          {evaluation.unit})
        </dd>
        <dt>Baseline</dt>
        <dd>
          <code>{evaluation.baseline}</code>
        </dd>
        <dt>Target</dt>
        <dd>
          <code>
            {evaluation.target.comparator} {evaluation.target.value}
          </code>
        </dd>
      </dl>

      <section aria-labelledby="guardrails-heading">
        <h2 id="guardrails-heading">Guardrails</h2>
        {evaluation.guardrails.length === 0 ? (
          <EmptyState title="No guardrails defined." />
        ) : (
          <ul className="definition-list">
            {evaluation.guardrails.map((g) => (
              <li key={g.name}>
                <strong>{g.name}</strong> — <code>{g.metric}</code>{" "}
                <code>
                  {g.comparator}
                  {g.value !== null ? ` ${g.value}` : ""}
                </code>
                {g.description ? ` — ${g.description}` : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </DefinitionPage>
  );
}
