/**
 * Save outcome feedback (authoring-flow.md save contract): success,
 * validation-blocked, conflict-with-reload, and retryable failure are
 * distinct states. The notice records what the last save attempt did;
 * the draft badge independently shows current dirty state.
 */

import type { SaveNotice } from "../workspace/store";

export function SaveNoticeView({
  notice,
  onReload,
  onRetrySave,
}: {
  notice: SaveNotice;
  /** Reload the active bundle from disk (draft preserved). */
  onReload: () => void;
  /** Retry the same save — for the `failed` outcome. */
  onRetrySave: () => void;
}) {
  switch (notice.outcome) {
    case "saved":
      return (
        <p role="status" className="state state-ok">
          Saved to disk. Active digest: <code>{notice.digest}</code>
        </p>
      );
    case "invalid":
      return (
        <div role="alert" className="state state-error">
          <p>
            <strong>Not saved — the draft bundle has validation errors.</strong>{" "}
            The active definitions were left untouched. Fix the diagnostics
            shown per definition and save again.
          </p>
        </div>
      );
    case "conflict":
      return (
        <div role="alert" className="state state-error">
          <p>
            <strong>
              Not saved — the workspace changed on disk since it was opened.
            </strong>{" "}
            The active digest is now <code>{notice.digest}</code>. Your draft
            is preserved; reload the active bundle to review what changed —
            saving after a reload overwrites the on-disk changes.
          </p>
          <button type="button" onClick={onReload}>
            Reload active bundle
          </button>
        </div>
      );
    case "failed":
      return (
        <div role="alert" className="state state-error">
          <p>
            <strong>Save failed.</strong>{" "}
            {notice.message ??
              "The workspace could not be written; the active bundle was left untouched."}
          </p>
          <button type="button" onClick={onRetrySave}>
            Retry save
          </button>
        </div>
      );
  }
}
