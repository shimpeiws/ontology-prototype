/**
 * Minimal raw-text draft editor for one workspace file.
 *
 * This is the free-form companion to the structured per-definition forms
 * (#16/#17/#18) and remains the escape hatch for content the forms do not
 * expose: edits mark the file dirty, "Revert to active" restores the exact
 * on-disk text after an explicit confirmation, and diagnostics for the file sit beside it. It never
 * validates locally — "Validate draft" sends the whole bundle to the API.
 */

import { useEffect, useState } from "react";

import { useWorkspace } from "../workspace/context";
import { isFileDirty } from "../workspace/store";
import type { WorkspaceFileName } from "../lib/types";

export function DraftEditor({ file }: { file: WorkspaceFileName }) {
  const { state, editFile, removeFile, revertFile } = useWorkspace();
  const draft = state.drafts[file] ?? null;
  const dirty = isFileDirty(state, file);
  const onDisk =
    state.workspace !== null &&
    Object.hasOwn(state.workspace.activeFiles, file);
  const labelId = `draft-editor-${file}`;
  const [confirmingRevert, setConfirmingRevert] = useState(false);

  // A confirmation only makes sense while there is something to discard;
  // a save or another revert path that cleans the file dismisses it.
  useEffect(() => {
    if (!dirty) setConfirmingRevert(false);
  }, [dirty]);

  return (
    <section className="draft-editor" aria-labelledby={labelId}>
      <div className="draft-editor-header">
        <h3 id={labelId}>
          Draft <code>{file}</code>
        </h3>
        <div className="draft-editor-actions">
          {dirty ? <span className="badge badge-draft">unsaved</span> : null}
          {draft === null ? (
            <span className="badge badge-missing">
              {onDisk ? "removed from draft" : "not on disk"}
            </span>
          ) : null}
          {draft !== null ? (
            <button type="button" onClick={() => removeFile(file)}>
              Remove from draft
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => setConfirmingRevert(true)}
            disabled={!dirty || state.saving || confirmingRevert}
            title={
              state.saving
                ? "A save is in flight — wait for it to settle before discarding the draft."
                : undefined
            }
          >
            Revert to active
          </button>
        </div>
      </div>
      {confirmingRevert ? (
        <div role="alert" className="state state-error">
          <p>
            Discard unsaved changes to <code>{file}</code> and restore the
            active text?
          </p>
          <button
            type="button"
            disabled={state.saving}
            onClick={() => {
              // A save already sent cannot be recalled; discarding now
              // would let the discarded edits still reach disk.
              if (state.saving) return;
              setConfirmingRevert(false);
              revertFile(file);
            }}
          >
            Discard changes
          </button>
          <button type="button" onClick={() => setConfirmingRevert(false)}>
            Keep editing
          </button>
        </div>
      ) : null}
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
            ? onDisk
              ? `${file} was removed from the draft — the on-disk file still exists. Typing here starts a new draft.`
              : `${file} does not exist on disk — text entered here becomes a draft file.`
            : undefined
        }
        onChange={(e) => editFile(file, e.target.value)}
      />
    </section>
  );
}
