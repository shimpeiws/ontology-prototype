import { Link } from "react-router";

import { useWorkspace } from "../workspace/context";
import { isValidationStale } from "../workspace/store";
import { DiagnosticsList } from "../components/DiagnosticsList";
import { FileStatusList } from "../components/DraftStatus";
import { EmptyState } from "../components/states";

export function meta() {
  return [{ title: "ontology — workspace overview" }];
}

export default function WorkspaceOverview() {
  const { state } = useWorkspace();
  const ws = state.workspace;
  if (!ws) return null;

  const definitions = ws.definitions;
  const stale = isValidationStale(state);

  return (
    <div className="workspace-overview">
      <h1>Workspace overview</h1>

      <section aria-labelledby="bundle-status">
        <h2 id="bundle-status">Bundle status</h2>
        <dl className="bundle-status">
          <dt>Active bundle</dt>
          <dd>
            <span
              className={`badge ${ws.valid ? "badge-valid" : "badge-invalid"}`}
            >
              {ws.valid ? "valid" : "invalid"}
            </span>
          </dd>
          <dt>Active digest</dt>
          <dd>
            <code>{ws.digest ?? "unavailable"}</code>
          </dd>
          <dt>Resolved path</dt>
          <dd>
            <code>{ws.resolvedPath}</code>
          </dd>
        </dl>
        <FileStatusList state={state} />
      </section>

      <section aria-labelledby="definition-links">
        <h2 id="definition-links">Definitions</h2>
        {definitions ? (
          <ul className="definition-links">
            <li>
              <Link to="/workspace/domain">Domain</Link> —{" "}
              {Object.keys(definitions.domain.entities).length} entities,{" "}
              {Object.keys(definitions.domain.events).length} events,{" "}
              {Object.keys(definitions.domain.projections).length} projections
            </li>
            <li>
              <Link to="/workspace/mapping">Mapping</Link> —{" "}
              {definitions.mapping.sources.length}{" "}
              {definitions.mapping.sources.length === 1 ? "source" : "sources"},{" "}
              {definitions.mapping.sources.reduce(
                (n, s) => n + s.entities.length,
                0,
              )}{" "}
              entity bindings,{" "}
              {definitions.mapping.sources.reduce(
                (n, s) => n + s.events.length,
                0,
              )}{" "}
              event bindings
            </li>
            <li>
              <Link to="/workspace/evaluation">Evaluation</Link> —{" "}
              {definitions.evaluation.metric} ({definitions.evaluation.unit})
            </li>
            <li>
              <Link to="/workspace/agent">Agent task</Link> —{" "}
              {Object.keys(definitions.agent.permitted_actions).length}{" "}
              permitted actions
            </li>
          </ul>
        ) : (
          <EmptyState title="Definitions could not be parsed.">
            <p>
              Fix the diagnostics below, then reload the workspace to inspect
              typed definitions.
            </p>
          </EmptyState>
        )}
      </section>

      <section aria-labelledby="diagnostics-heading">
        <h2 id="diagnostics-heading">
          Diagnostics {stale ? "(stale — draft changed)" : null}
        </h2>
        {state.validation && !stale ? (
          <p className="hint">
            Showing draft validation results (draft revision{" "}
            {state.validation.revision}).
          </p>
        ) : state.validation && stale ? (
          <p className="hint">
            The draft changed after this validation ran — results below are
            stale. Run “Validate draft” again.
          </p>
        ) : (
          <p className="hint">Showing diagnostics for the active bundle.</p>
        )}
        <DiagnosticsList
          diagnostics={
            state.validation ? state.validation.diagnostics : ws.diagnostics
          }
        />
      </section>
    </div>
  );
}
