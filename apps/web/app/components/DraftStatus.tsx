/**
 * Draft/active/unsaved status indicators (authoring-flow.md UI states):
 * each definition and the bundle expose whether they match the active
 * (on-disk) content, and whether shown validation is stale.
 */

import { Tag } from "antd";

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
      <Tag
        className={`badge ${dirty ? "badge-draft" : "badge-active"}`}
        color={dirty ? "warning" : "success"}
      >
        {dirty ? "unsaved draft" : "active"}
      </Tag>
      {state.saving ? (
        <Tag className="badge badge-validating" role="status">
          saving…
        </Tag>
      ) : null}
      {state.validating ? (
        <Tag className="badge badge-validating" role="status">
          validating…
        </Tag>
      ) : state.validation ? (
        <Tag
          className={`badge ${
            stale
              ? "badge-stale"
              : state.validation.valid
                ? "badge-valid"
                : "badge-invalid"
          }`}
          color={stale ? "warning" : state.validation.valid ? "success" : "error"}
        >
          {stale
            ? "validation stale"
            : state.validation.valid
              ? "draft valid"
              : "draft invalid"}
        </Tag>
      ) : null}
      {state.validationError ? (
        <Tag className="badge badge-error" color="error" role="status">
          validation failed
        </Tag>
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
        <Tag className="badge badge-draft" color="warning">
          unsaved
        </Tag>
      ) : !onDisk ? (
        <Tag className="badge badge-missing" color="warning">
          missing
        </Tag>
      ) : (
        <Tag className="badge badge-active" color="success">
          active
        </Tag>
      )}
    </li>
  );
}
