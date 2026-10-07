import { useWorkspace } from "../workspace/context";
import { DefinitionPage } from "../components/DefinitionPage";
import { EmptyState } from "../components/states";
import { MappingForm } from "../components/MappingForm";
import { PreviewPanel } from "../components/PreviewPanel";
import type { FieldAssignment, SourceMapping } from "../lib/types";

export function meta() {
  return [{ title: "ontology — mapping" }];
}

function formatAssignment(a: FieldAssignment): string {
  if (a.field) return `field ${a.field}${a.cast ? ` as ${a.cast}` : ""}`;
  return `literal ${JSON.stringify(a.literal)}`;
}

function SourceSection({ source, index }: { source: SourceMapping; index: number }) {
  return (
    <section aria-labelledby={`source-${index}-heading`}>
      <h2 id={`source-${index}-heading`}>
        Source <code>{source.name}</code> ({source.format})
      </h2>

      <section aria-labelledby={`source-${index}-entities-heading`}>
        <h3 id={`source-${index}-entities-heading`}>Entity bindings</h3>
        {source.entities.length === 0 ? (
          <EmptyState title="No entity bindings defined yet." />
        ) : (
          <ul className="definition-list">
            {source.entities.map((binding, i) => (
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

      <section aria-labelledby={`source-${index}-events-heading`}>
        <h3 id={`source-${index}-events-heading`}>Event bindings</h3>
        {source.events.length === 0 ? (
          <EmptyState title="No event bindings defined yet." />
        ) : (
          <ul className="definition-list">
            {source.events.map((binding, i) => (
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
    </section>
  );
}

export default function MappingPage() {
  const { state } = useWorkspace();
  const mapping = state.workspace?.definitions?.mapping;
  const editor = (
    <>
      <MappingForm />
      <PreviewPanel />
    </>
  );
  if (!mapping) {
    return <DefinitionPage title="Mapping" file="mapping.yaml" form={editor} />;
  }

  return (
    <DefinitionPage title="Mapping" file="mapping.yaml" form={editor}>
      <p>
        <strong>{mapping.name}</strong>
        {mapping.description ? ` — ${mapping.description}` : null}
      </p>
      <p>
        {mapping.sources.length === 1 ? "Source" : "Sources"}:{" "}
        <code>{mapping.sources.map((s) => s.name).join(", ")}</code>
      </p>

      {mapping.sources.map((source, i) => (
        <SourceSection key={`${source.name}-${i}`} source={source} index={i} />
      ))}
    </DefinitionPage>
  );
}
