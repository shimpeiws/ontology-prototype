import { useWorkspace } from "../workspace/context";
import { DefinitionPage } from "../components/DefinitionPage";
import { EmptyState } from "../components/states";
import type { PropertySpec } from "../lib/types";

export function meta() {
  return [{ title: "ontology — domain" }];
}

function PropertyLine({ spec }: { spec: PropertySpec }) {
  return (
    <li>
      <code>{spec.name}</code>: {spec.type}
      {spec.ref ? <code>{` → ${spec.ref}`}</code> : null}
      {spec.required ? " (required)" : null}
      {spec.description ? ` — ${spec.description}` : null}
    </li>
  );
}

export default function DomainPage() {
  const { state } = useWorkspace();
  const domain = state.workspace?.definitions?.domain;
  if (!domain) return <DefinitionPage title="Domain" file="domain.yaml" />;

  return (
    <DefinitionPage title="Domain" file="domain.yaml">
      <p>
        <strong>{domain.name}</strong>
        {domain.description ? ` — ${domain.description}` : null}
      </p>

      <section aria-labelledby="entities-heading">
        <h2 id="entities-heading">Entities</h2>
        {Object.keys(domain.entities).length === 0 ? (
          <EmptyState title="No entities defined yet." />
        ) : (
          <ul className="definition-list">
            {Object.values(domain.entities).map((entity) => (
              <li key={entity.name}>
                <strong>{entity.name}</strong>
                {entity.description ? ` — ${entity.description}` : null}
                <ul>
                  {Object.values(entity.properties).map((p) => (
                    <PropertyLine key={p.name} spec={p} />
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="events-heading">
        <h2 id="events-heading">Events</h2>
        {Object.keys(domain.events).length === 0 ? (
          <EmptyState title="No events defined yet." />
        ) : (
          <ul className="definition-list">
            {Object.values(domain.events).map((event) => (
              <li key={event.name}>
                <strong>{event.name}</strong> on <code>{event.subject}</code>
                {event.description ? ` — ${event.description}` : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="projections-heading">
        <h2 id="projections-heading">Projections</h2>
        {Object.keys(domain.projections).length === 0 ? (
          <EmptyState title="No projections defined yet." />
        ) : (
          <ul className="definition-list">
            {Object.values(domain.projections).map((p) => (
              <li key={p.name}>
                <strong>{p.name}</strong> on <code>{p.subject}</code>
                {p.description ? ` — ${p.description}` : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </DefinitionPage>
  );
}
