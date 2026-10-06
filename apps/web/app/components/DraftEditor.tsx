/**
 * Minimal raw-text draft editor for one workspace file.
 *
 * Structured per-definition forms belong to #16/#17/#18; this editor exists
 * so draft/active/unsaved state is real in #15: edits mark the file dirty,
 * "Revert to active" restores the exact on-disk text, and diagnostics for
 * the file sit beside it. It never validates locally — "Validate draft"
 * sends the whole bundle to the API.
 */

import { useWorkspace } from "../workspace/context";
import { isFileDirty } from "../workspace/store";
import type { WorkspaceFileName } from "../lib/types";

export function DraftEditor({ file }: { file: WorkspaceFileName }) {
  const { state, editFile, removeFile, revertFile } = useWorkspace();
  const draft = state.drafts[file] ?? null;
  const dirty = isFileDirty(state, file);
  const labelId = `draft-editor-${file}`;

  return (
    <section className="draft-editor" aria-labelledby={labelId}>
      <div className="draft-editor-header">
        <h3 id={labelId}>
          Draft <code>{file}</code>
        </h3>
        <div className="draft-editor-actions">
          {dirty ? <span className="badge badge-draft">unsaved</span> : null}
          {draft === null ? (
            <span className="badge badge-missing">not on disk</span>
          ) : null}
          {draft !== null ? (
            <button type="button" onClick={() => removeFile(file)}>
              Remove from draft
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => revertFile(file)}
            disabled={!dirty}
          >
            Revert to active
          </button>
        </div>
      </div>
      <label className="sr-only" htmlFor={`${labelId}-textarea`}>
        {file} draft YAML
      </label>
      <textarea
        id={`${labelId}-textarea`}
        className="draft-editor-textarea"
        spellCheck={false}
        value={draft ?? ""}
        placeholder={
          draft === null
            ? `${file} does not exist on disk — text entered here becomes a draft file.`
            : undefined
        }
        onChange={(e) => editFile(file, e.target.value)}
      />
    </section>
  );
}
