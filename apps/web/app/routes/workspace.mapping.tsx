import { useWorkspace } from "../workspace/context";
import { DefinitionPage } from "../components/DefinitionPage";
import { EmptyState } from "../components/states";
import type { FieldAssignment } from "../lib/types";

export function meta() {
  return [{ title: "ontology — mapping" }];
}

function formatAssignment(a: FieldAssignment): string {
  if (a.field) return `field ${a.field}${a.cast ? ` as ${a.cast}` : ""}`;
  return `literal ${JSON.stringify(a.literal)}`;
}

export default function MappingPage() {
  const { state } = useWorkspace();
  const mapping = state.workspace?.definitions?.mapping;
  if (!mapping) {
    return <DefinitionPage title="Mapping" file="mapping.yaml" />;
  }

  return (
    <DefinitionPage title="Mapping" file="mapping.yaml">
      <p>
        <strong>{mapping.name}</strong>
        {mapping.description ? ` — ${mapping.description}` : null}
      </p>
      <p>
        Source: <code>{mapping.source.name}</code> (
        {mapping.source.format})
      </p>

      <section aria-labelledby="entity-bindings-heading">
        <h2 id="entity-bindings-heading">Entity bindings</h2>
        {mapping.entities.length === 0 ? (
          <EmptyState title="No entity bindings defined yet." />
        ) : (
          <ul className="definition-list">
            {mapping.entities.map((binding, i) => (
              <li key={`${binding.entity}-${i}`}>
                <strong>{binding.entity}</strong> — id:{" "}
                {formatAssignment(binding.id)}
                <ul>
                  {Object.entries(binding.properties).map(([name, a]) => (
                    <li key={name}>
                      <code>{name}</code> ← {formatAssignment(a)}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="event-bindings-heading">
        <h2 id="event-bindings-heading">Event bindings</h2>
        {mapping.events.length === 0 ? (
          <EmptyState title="No event bindings defined yet." />
        ) : (
          <ul className="definition-list">
            {mapping.events.map((binding, i) => (
              <li key={`${binding.event}-${i}`}>
                <strong>{binding.event}</strong> — subject{" "}
                <code>{binding.subject.entity}</code> (
                {formatAssignment(binding.subject.id)}), occurred_at:{" "}
                {formatAssignment(binding.occurred_at)}
                {binding.when ? (
                  <div className="hint">
                    when <code>{JSON.stringify(binding.when.raw)}</code>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </DefinitionPage>
  );
}
