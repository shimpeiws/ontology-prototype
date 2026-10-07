/**
 * Draft mapping preview panel (#17): sends a bounded CSV/JSON sample plus
 * the current draft bundle to POST /api/v1/workspaces/drafts/preview and
 * renders the shared-core result — each source row beside the canonical
 * entities/events it resolved to, with evidence references (observation id,
 * preview id, digests, row) and row/field diagnostics.
 *
 * Nothing persists: the server runs an isolated in-memory preview and the
 * client keeps only component state. A result is keyed to the draft
 * revision and sample text it ran against — editing any draft or the
 * sample marks the shown result stale until a new run lands. The button
 * single-flights the request, so a superseded response can never land.
 */

import { useId, useState } from "react";

import { api, ApiError } from "../lib/api";
import { contractVocabulary, useContract } from "../lib/contract";
import { draftSourceFormat, draftSourceNames } from "../lib/draftDoc";
import { draftFilesForRequest } from "../workspace/store";
import type {
  DraftPreviewResponse,
  PreviewObservation,
  PreviewPayload,
} from "../lib/types";
import { useWorkspace } from "../workspace/context";
import { DiagnosticsList } from "./DiagnosticsList";
import { EmptyState, ErrorState, LoadingState } from "./states";

/** One preview invocation and its outcome, keyed to what it ran against. */
interface PreviewRun {
  /** Workspace path the preview ran against — revisions reset per workspace. */
  path: string;
  /** Draft revision captured when the request was sent. */
  revision: number;
  /** Sample text captured when the request was sent. */
  sample: string;
  /** Declared source the sample resolved through ("" = core default). */
  source: string;
  status: "running" | "done" | "failed";
  response?: DraftPreviewResponse;
  error?: ApiError | Error;
}

function toError(err: unknown): ApiError | Error {
  return err instanceof Error ? err : new Error(String(err));
}

function ObservationCard({ observation }: { observation: PreviewObservation }) {
  const fields = Object.entries(observation.record);
  return (
    <section className="form-card preview-observation">
      <p>
        <span
          className={`badge ${
            observation.status === "accepted" ? "badge-valid" : "badge-invalid"
          }`}
        >
          {observation.status}
        </span>{" "}
        <strong>
          Record row {observation.row}
        </strong>{" "}
        <code>{observation.observation_id}</code>
      </p>

      {fields.length ? (
        <table className="preview-record">
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Normalized value</th>
            </tr>
          </thead>
          <tbody>
            {fields.map(([name, value]) => (
              <tr key={name}>
                <td>
                  <code>{name}</code>
                </td>
                <td>
                  {value === null ? (
                    <em className="preview-null">null</em>
                  ) : (
                    <code>{String(value)}</code>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {observation.entities.length ? (
        <div>
          <p className="hint">Entities</p>
          <ul>
            {observation.entities.map((e, i) => (
              <li key={i}>
                <code>
                  {e.entity}/{e.id}
                </code>
                {Object.keys(e.properties).length ? (
                  <ul>
                    {Object.entries(e.properties).map(([k, v]) => (
                      <li key={k}>
                        <code>{k}</code>:{" "}
                        {v === null ? (
                          <em className="preview-null">null</em>
                        ) : (
                          <code>{String(v)}</code>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {observation.events.length ? (
        <div>
          <p className="hint">Events</p>
          <ul>
            {observation.events.map((ev, i) => (
              <li key={i}>
                <code>{ev.event}</code> on{" "}
                <code>
                  {ev.subject.entity}/{ev.subject.id}
                </code>{" "}
                at <code>{ev.occurred_at}</code> (observed{" "}
                <code>{ev.observed_at}</code>)
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {observation.diagnostics.length ? (
        <DiagnosticsList diagnostics={observation.diagnostics} />
      ) : null}
    </section>
  );
}

function PreviewResult({ preview }: { preview: PreviewPayload }) {
  // The core appends per-record diagnostics to the run list; they render
  // inside each observation card, so the header list shows run-level
  // findings (encoding, truncation, empty sample) only.
  const runDiagnostics = preview.diagnostics.filter(
    (d) => d.path === null || !d.path.startsWith("record["),
  );
  return (
    <div>
      <dl className="definition-facts">
        <dt>Source</dt>
        <dd>
          <code>
            {preview.source ?? "—"}.{preview.format ?? "—"}
          </code>
        </dd>
        <dt>Preview id</dt>
        <dd>
          <code>{preview.preview_id ?? "—"}</code>
        </dd>
        <dt>Input digest</dt>
        <dd>
          <code>{preview.input_digest ?? "—"}</code>
        </dd>
        <dt>Mapping digest</dt>
        <dd>
          <code>{preview.mapping_digest ?? "—"}</code>
        </dd>
        <dt>Observed at</dt>
        <dd>
          <code>{preview.observed_at ?? "—"}</code>
        </dd>
        <dt>Records</dt>
        <dd>
          {preview.record_count}
          {preview.truncated ? (
            <>
              {" "}
              <span className="badge badge-stale">truncated</span>
            </>
          ) : null}
        </dd>
      </dl>

      {runDiagnostics.length ? (
        <DiagnosticsList diagnostics={runDiagnostics} />
      ) : null}

      {preview.observations.length ? (
        preview.observations.map((o, i) => (
          <ObservationCard key={o.observation_id ?? i} observation={o} />
        ))
      ) : (
        <EmptyState title="The sample produced no records to preview." />
      )}
    </div>
  );
}

export function PreviewPanel() {
  const { state } = useWorkspace();
  const { contract } = useContract();
  const limits = contractVocabulary(contract)?.previewLimits ?? null;
  const sampleId = useId();
  const sourceId = useId();

  const mappingDraft = state.drafts["mapping.yaml"] ?? null;
  const mappingFallback = state.workspace?.definitions?.mapping ?? null;
  const sources = draftSourceNames(mappingDraft, mappingFallback);
  const [source, setSource] = useState("");
  // A remembered selection is only honored while the current draft still
  // declares that source — after a workspace switch or a source removal the
  // stale name must not leak into the request.
  const effectiveSource = sources.includes(source)
    ? source
    : sources[0] || "";
  const format = draftSourceFormat(
    mappingDraft,
    mappingFallback,
    effectiveSource || null,
  );

  const [sample, setSample] = useState("");
  const [run, setRun] = useState<PreviewRun | null>(null);

  const sampleBytes = new TextEncoder().encode(sample).length;
  const oversize = limits !== null && sampleBytes > limits.maxBytes;

  const runPreview = async () => {
    if (!state.workspace) return;
    const path = state.workspace.requestedPath;
    const revision = state.draftRevision;
    const text = sample;
    const sourceName = effectiveSource;
    setRun({ status: "running", path, revision, sample: text, source: sourceName });
    try {
      const response = await api.previewDraft(
        path,
        draftFilesForRequest(state.drafts),
        sourceName ? { text, source: sourceName } : { text },
      );
      setRun({
        status: "done",
        path,
        revision,
        sample: text,
        source: sourceName,
        response,
      });
    } catch (err) {
      setRun({
        status: "failed",
        path,
        revision,
        sample: text,
        source: sourceName,
        error: toError(err),
      });
    }
  };

  // A shown result describes exactly the workspace, draft revision, sample,
  // and source it ran against — any drift since means the display is stale,
  // not current. The path is part of the key because opening another
  // workspace resets the draft revision, which could otherwise collide.
  const stale =
    run !== null &&
    (run.path !== (state.workspace?.requestedPath ?? null) ||
      run.revision !== state.draftRevision ||
      run.sample !== sample ||
      run.source !== effectiveSource);
  const running = run?.status === "running";

  return (
    <section className="preview" aria-labelledby="preview-heading">
      <h2 id="preview-heading">Preview mapping</h2>
      <p className="hint">
        Resolves the draft mapping against a pasted sample via the shared
        core — isolated from the workspace files and the canonical store.
        {format ? ` The selected source declares format ${format}.` : ""}
        {limits
          ? ` Samples are bounded to ${limits.maxBytes} bytes and ${limits.maxRecords} records.`
          : ""}
      </p>

      {sources.length > 1 ? (
        <div className="form-field">
          <label htmlFor={sourceId}>Source</label>
          <select
            id={sourceId}
            value={effectiveSource}
            onChange={(e) => setSource(e.target.value)}
          >
            {sources.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div className="form-field">
        <label htmlFor={sampleId}>
          Sample input ({format ?? "csv or json"})
        </label>
        <textarea
          id={sampleId}
          rows={8}
          value={sample}
          spellCheck={false}
          onChange={(e) => setSample(e.target.value)}
        />
        {oversize ? (
          <p className="hint" role="alert">
            Sample is {sampleBytes} bytes — over the {limits?.maxBytes}-byte
            limit; the preview will report PREVIEW.SAMPLE_TOO_LARGE.
          </p>
        ) : null}
      </div>

      <button
        type="button"
        disabled={running || !state.workspace}
        onClick={() => void runPreview()}
      >
        {running ? "Running preview…" : "Run preview"}
      </button>

      <div aria-live="polite">
        {stale ? (
          <p className="field-diagnostics stale" role="status">
            <span className="badge badge-stale">stale</span> The draft or the
            sample changed since this preview ran — run it again to see
            current results.
          </p>
        ) : null}

        {run?.status === "running" ? (
          <LoadingState label="Running preview against the draft…" />
        ) : run?.status === "failed" && run.error ? (
          <ErrorState
            error={run.error}
            onRetry={run.error instanceof ApiError && run.error.retryable
              ? () => void runPreview()
              : undefined}
          />
        ) : run?.status === "done" && run.response ? (
          run.response.preview === null ? (
            <>
              <EmptyState title="No preview — the draft bundle did not validate." />
              <DiagnosticsList diagnostics={run.response.diagnostics} />
            </>
          ) : (
            <PreviewResult preview={run.response.preview} />
          )
        ) : null}
      </div>
    </section>
  );
}
