/**
 * Minimal raw-text draft editor for one workspace file.
 *
 * This is the free-form companion to the structured per-definition forms
 * (#16/#17/#18) and remains the escape hatch for content the forms do not
 * expose: edits mark the file dirty, "Revert to active" restores the exact
 * on-disk text behind an explicit confirmation, and diagnostics for the
 * file sit beside it. It never validates locally — "Validate draft" sends
 * the whole bundle to the API.
 */

import { useEffect, useState } from "react";
import { Alert, Button, Tag } from "antd";

import { useWorkspace } from "../workspace/context";
import { isFileDirty } from "../workspace/store";
import type { OpenedWorkspace } from "../workspace/store";
import type { WorkspaceFileName } from "../lib/types";

export function DraftEditor({ file }: { file: WorkspaceFileName }) {
  const { state, editFile, removeFile, revertFile } = useWorkspace();
  const draft = state.drafts[file] ?? null;
  const dirty = isFileDirty(state, file);
  const onDisk =
    state.workspace !== null &&
    Object.hasOwn(state.workspace.activeFiles, file);
  const labelId = `draft-editor-${file}`;
  // Revert discards the draft text, so it arms a confirmation first — the
  // same two-step pattern as closing a dirty workspace. The arm is bound
  // to the active baseline it described: the reducer replaces the
  // workspace object whenever that baseline moves (a landed save or a
  // reload), so an arm taken against the old baseline can never fire a
  // revert against different on-disk text. It is moot once the file is
  // clean (a revert-all or a landed save), and a settling save disarms it
  // too — the baseline the confirmation described may move when the save
  // lands.
  const [armedBaseline, setArmedBaseline] =
    useState<OpenedWorkspace | null>(null);
  const confirmingRevert =
    armedBaseline !== null && armedBaseline === state.workspace;
  useEffect(() => {
    if (!confirmingRevert || !dirty || state.saving) setArmedBaseline(null);
  }, [confirmingRevert, dirty, state.saving]);

  return (
    <section className="draft-editor" aria-labelledby={labelId}>
      <div className="draft-editor-header">
        <h3 id={labelId}>
          Draft <code>{file}</code>
        </h3>
        <div className="draft-editor-actions">
          {dirty ? <Tag className="badge badge-draft">unsaved</Tag> : null}
          {draft === null ? (
            <Tag className="badge badge-missing" color="warning">
              {onDisk ? "removed from draft" : "not on disk"}
            </Tag>
          ) : null}
          {draft !== null ? (
            <Button
              size="small"
              onClick={() => removeFile(file)}
              disabled={state.saving}
              title={
                state.saving
                  ? "A save is in flight — wait for it to settle before discarding edits."
                  : undefined
              }
            >
              Remove from draft
            </Button>
          ) : null}
          {confirmingRevert && dirty && !state.saving ? (
            <>
              <Button
                size="small"
                danger
                onClick={() => {
                  // Same guard as the other discard paths: a save already
                  // sent cannot be recalled.
                  if (state.saving) return;
                  revertFile(file);
                  setArmedBaseline(null);
                }}
              >
                Discard changes
              </Button>
              <Button
                size="small"
                onClick={() => setArmedBaseline(null)}
              >
                Keep editing
              </Button>
            </>
          ) : (
            <Button
              size="small"
              onClick={() => setArmedBaseline(state.workspace)}
              disabled={!dirty || state.saving}
              title={
                state.saving
                  ? "A save is in flight — wait for it to settle before discarding edits."
                  : undefined
              }
            >
              Revert to active
            </Button>
          )}
        </div>
      </div>
      {confirmingRevert && dirty && !state.saving ? (
        <Alert
          className="state state-error"
          type="warning"
          role="alert"
          title={
            <>
              Unsaved changes to <code>{file}</code> will be discarded — the
              file returns to the active on-disk text.
            </>
          }
        />
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
