/**
 * Draft/active/unsaved status indicators (authoring-flow.md UI states):
 * each definition and the bundle expose whether they match the active
 * (on-disk) content, and whether shown validation is stale.
 */

import {
  isDraftDirty,
  isFileDirty,
  isValidationStale,
} from "../workspace/store";
import type { WorkspaceState } from "../workspace/store";
import type { WorkspaceFileName } from "../lib/types";
import { WORKSPACE_FILE_NAMES } from "../lib/types";

export function DraftStatusBadge({ state }: { state: WorkspaceState }) {
  if (!state.workspace) return null;
  const dirty = isDraftDirty(state);
  const stale = isValidationStale(state);
  return (
    <div className="draft-status" aria-live="polite">
      <span className={`badge ${dirty ? "badge-draft" : "badge-active"}`}>
        {dirty ? "unsaved draft" : "active"}
      </span>
      {state.saving ? (
        <span className="badge badge-validating" role="status">
          saving…
        </span>
      ) : null}
      {state.validating ? (
        <span className="badge badge-validating" role="status">
          validating…
        </span>
      ) : state.validation ? (
        <span
          className={`badge ${
            stale
              ? "badge-stale"
              : state.validation.valid
                ? "badge-valid"
                : "badge-invalid"
          }`}
        >
          {stale
            ? "validation stale"
            : state.validation.valid
              ? "draft valid"
              : "draft invalid"}
        </span>
      ) : null}
      {state.validationError ? (
        <span className="badge badge-error" role="status">
          validation failed
        </span>
      ) : null}
    </div>
  );
}

export function FileStatusList({ state }: { state: WorkspaceState }) {
  return (
    <ul className="file-status-list">
      {WORKSPACE_FILE_NAMES.map((name) => (
        <FileStatus key={name} state={state} file={name} />
      ))}
    </ul>
  );
}

function FileStatus({
  state,
  file,
}: {
  state: WorkspaceState;
  file: WorkspaceFileName;
}) {
  const onDisk =
    state.workspace !== null &&
    Object.hasOwn(state.workspace.activeFiles, file);
  const dirty = isFileDirty(state, file);
  return (
    <li>
      <code>{file}</code>{" "}
      {dirty ? (
        <span className="badge badge-draft">unsaved</span>
      ) : !onDisk ? (
        <span className="badge badge-missing">missing</span>
      ) : (
        <span className="badge badge-active">active</span>
      )}
    </li>
  );
}
