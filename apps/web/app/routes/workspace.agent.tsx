import { useWorkspace } from "../workspace/context";
import { DefinitionPage } from "../components/DefinitionPage";
import { AgentForm } from "../components/AgentForm";
import { EmptyState } from "../components/states";

export function meta() {
  return [{ title: "ontology — agent task" }];
}

export default function AgentPage() {
  const { state } = useWorkspace();
  const agent = state.workspace?.definitions?.agent;
  if (!agent) {
    return (
      <DefinitionPage
        title="Agent task"
        file="agent.yaml"
        form={<AgentForm />}
      />
    );
  }

  return (
    <DefinitionPage
      title="Agent task"
      file="agent.yaml"
      form={<AgentForm />}
    >
      <p>
        <strong>{agent.name}</strong>
        {agent.description ? ` — ${agent.description}` : null}
      </p>

      <dl className="definition-facts">
        <dt>Projection</dt>
        <dd>
          <code>{agent.projection}</code>
        </dd>
        <dt>Goal</dt>
        <dd>{agent.task.goal}</dd>
        <dt>Deliverables</dt>
        <dd>
          {agent.task.deliverables.map((d) => (
            <code key={d} className="deliverable">
              {d}
            </code>
          ))}
        </dd>
      </dl>

      <section aria-labelledby="actions-heading">
        <h2 id="actions-heading">Permitted actions</h2>
        {Object.keys(agent.permitted_actions).length === 0 ? (
          <EmptyState title="No permitted actions defined." />
        ) : (
          <ul className="definition-list">
            {Object.values(agent.permitted_actions).map((action) => (
              <li key={action.name}>
                <strong>{action.name}</strong>
                {action.description ? ` — ${action.description}` : null}
                <ul>
                  {Object.values(action.params).map((p) => (
                    <li key={p.name}>
                      <code>{p.name}</code>: {p.type}
                      {p.ref ? <code>{` → ${p.ref}`}</code> : null}
                      {p.required ? " (required)" : null}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </section>
    </DefinitionPage>
  );
}
