/**
 * Store tests — synthetic fixture mode (fixtures.ts). These cover the
 * draft/active invariants: edits never touch the active layer, revert is
 * byte-exact, stale results are flagged, superseded responses are dropped.
 */

import { describe, expect, it } from "vitest";

import {
  draftFilesForRequest,
  initialWorkspaceState,
  isDraftDirty,
  isFileDirty,
  isValidationStale,
  workspaceReducer,
} from "../workspace/store";
import type { WorkspaceState } from "../workspace/store";
import { ApiError } from "../lib/api";
import type { SaveResponse } from "../lib/types";
import {
  SYNTHETIC_DIAGNOSTIC,
  SYNTHETIC_OPEN_RESPONSE,
} from "./fixtures";

function saveResponse(overrides: Partial<SaveResponse>): SaveResponse {
  return {
    api_version: "v1",
    outcome: "saved",
    valid: true,
    digest: "saved-digest",
    definitions: null,
    diagnostics: [],
    message: null,
    ...overrides,
  };
}

function opened(): WorkspaceState {
  return workspaceReducer(initialWorkspaceState, {
    type: "opened",
    requestedPath: "job-invoicing",
    response: SYNTHETIC_OPEN_RESPONSE,
  });
}

function dirtyState(): WorkspaceState {
  return workspaceReducer(opened(), {
    type: "edited",
    file: "domain.yaml",
    text: "changed: true",
  });
}

describe("workspace store", () => {
  it("opened seeds drafts from active files and is clean", () => {
    const state = opened();
    expect(state.workspace?.resolvedPath).toBe("/abs/example/job-invoicing");
    expect(state.workspace?.digest).toBe("synthetic-digest");
    expect(state.drafts["domain.yaml"]).toBe(
      SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"],
    );
    expect(isDraftDirty(state)).toBe(false);
  });

  it("editing marks the file dirty without touching the active layer", () => {
    const state = opened();
    const edited = workspaceReducer(state, {
      type: "edited",
      file: "domain.yaml",
      text: "changed: true",
    });
    expect(isFileDirty(edited, "domain.yaml")).toBe(true);
    expect(isDraftDirty(edited)).toBe(true);
    expect(edited.workspace?.activeFiles["domain.yaml"]).toBe(
      SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"],
    );
    expect(edited.workspace?.digest).toBe("synthetic-digest");
  });

  it("revert restores the exact active text and clears dirty", () => {
    const state = opened();
    const edited = workspaceReducer(state, {
      type: "edited",
      file: "domain.yaml",
      text: "changed: true",
    });
    const reverted = workspaceReducer(edited, {
      type: "reverted",
      file: "domain.yaml",
    });
    expect(reverted.drafts["domain.yaml"]).toBe(
      SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"],
    );
    expect(isDraftDirty(reverted)).toBe(false);
  });

  it("draft edit invalidates the displayed validation result", () => {
    let state = opened();
    state = workspaceReducer(state, {
      type: "validationStarted",
      requestId: 1,
    });
    state = workspaceReducer(state, {
      type: "validationFinished",
      requestId: 1,
      revision: state.draftRevision,
      valid: true,
      digest: "d1",
      diagnostics: [],
    });
    expect(isValidationStale(state)).toBe(false);

    state = workspaceReducer(state, {
      type: "edited",
      file: "mapping.yaml",
      text: "x",
    });
    expect(isValidationStale(state)).toBe(true);
  });

  it("a superseded validation response cannot overwrite newer state", () => {
    let state = opened();
    state = workspaceReducer(state, { type: "validationStarted", requestId: 1 });
    state = workspaceReducer(state, { type: "validationStarted", requestId: 2 });
    // Late response from the first request must be discarded.
    state = workspaceReducer(state, {
      type: "validationFinished",
      requestId: 1,
      revision: 0,
      valid: true,
      digest: "stale",
      diagnostics: [],
    });
    expect(state.validating).toBe(true);
    expect(state.validation).toBeNull();

    state = workspaceReducer(state, {
      type: "validationFinished",
      requestId: 2,
      revision: 0,
      valid: false,
      digest: "fresh",
      diagnostics: [SYNTHETIC_DIAGNOSTIC],
    });
    expect(state.validation?.digest).toBe("fresh");
  });

  it("validation failure records the error, not a diagnostic", () => {
    let state = opened();
    state = workspaceReducer(state, { type: "validationStarted", requestId: 1 });
    state = workspaceReducer(state, {
      type: "validationFailed",
      requestId: 1,
      error: new ApiError("API.PATH_OUTSIDE_ROOTS", "nope", 403, false),
    });
    expect(state.validationError?.code).toBe("API.PATH_OUTSIDE_ROOTS");
    expect(state.validation).toBeNull();
    expect(state.validating).toBe(false);
  });

  it("draftFilesForRequest omits absent files — empty string is a real file", () => {
    let state = opened();
    state = workspaceReducer(state, {
      type: "fileRemoved",
      file: "agent.yaml",
    });
    state = workspaceReducer(state, {
      type: "edited",
      file: "evaluation.yaml",
      text: "",
    });
    const files = draftFilesForRequest(state.drafts);
    expect(Object.keys(files)).not.toContain("agent.yaml");
    expect(files["evaluation.yaml"]).toBe("");
  });

  it("revert restores a file that was removed from the draft", () => {
    let state = opened();
    state = workspaceReducer(state, {
      type: "fileRemoved",
      file: "agent.yaml",
    });
    expect(state.drafts["agent.yaml"]).toBeNull();

    state = workspaceReducer(state, {
      type: "reverted",
      file: "agent.yaml",
    });
    expect(state.drafts["agent.yaml"]).toBe(
      SYNTHETIC_OPEN_RESPONSE.files["agent.yaml"],
    );
    expect(isDraftDirty(state)).toBe(false);
  });

  it("revert invalidates the displayed validation result like an edit", () => {
    let state = dirtyState();
    state = workspaceReducer(state, {
      type: "validationStarted",
      requestId: 1,
    });
    state = workspaceReducer(state, {
      type: "validationFinished",
      requestId: 1,
      revision: state.draftRevision,
      valid: true,
      digest: "d1",
      diagnostics: [],
    });
    expect(isValidationStale(state)).toBe(false);

    state = workspaceReducer(state, {
      type: "reverted",
      file: "domain.yaml",
    });
    expect(isValidationStale(state)).toBe(true);
  });

  it("revertedAll restores every file to the exact active text", () => {
    let state = opened();
    state = workspaceReducer(state, {
      type: "edited",
      file: "domain.yaml",
      text: "changed: true",
    });
    state = workspaceReducer(state, {
      type: "fileRemoved",
      file: "agent.yaml",
    });
    expect(isDraftDirty(state)).toBe(true);

    state = workspaceReducer(state, { type: "revertedAll" });
    expect(isDraftDirty(state)).toBe(false);
    expect(state.drafts["domain.yaml"]).toBe(
      SYNTHETIC_OPEN_RESPONSE.files["domain.yaml"],
    );
    expect(state.drafts["agent.yaml"]).toBe(
      SYNTHETIC_OPEN_RESPONSE.files["agent.yaml"],
    );
  });

  it("close returns to the initial state", () => {
    const state = workspaceReducer(opened(), { type: "closed" });
    expect(state.workspace).toBeNull();
    expect(isDraftDirty(state)).toBe(false);
  });

  it("a saved outcome makes the sent files the new active bundle", () => {
    let state = dirtyState();
    const sentFiles = draftFilesForRequest(state.drafts);
    const revision = state.draftRevision;
    state = workspaceReducer(state, { type: "saveStarted", requestId: 1 });
    state = workspaceReducer(state, {
      type: "saveFinished",
      requestId: 1,
      revision,
      files: sentFiles,
      response: saveResponse({}),
    });

    expect(state.saving).toBe(false);
    expect(state.saveNotice?.outcome).toBe("saved");
    expect(state.workspace?.digest).toBe("saved-digest");
    expect(state.workspace?.activeFiles["domain.yaml"]).toBe(
      "changed: true",
    );
    expect(isDraftDirty(state)).toBe(false);
    // The save ran whole-bundle validation server-side; its diagnostics
    // describe the saved revision.
    expect(state.validation?.revision).toBe(revision);
    expect(isValidationStale(state)).toBe(false);
  });

  it("edits during an in-flight save stay dirty against the new baseline", () => {
    let state = dirtyState();
    const sentFiles = draftFilesForRequest(state.drafts);
    const revision = state.draftRevision;
    state = workspaceReducer(state, { type: "saveStarted", requestId: 1 });
    // The FDE keeps typing while the save request is in flight.
    state = workspaceReducer(state, {
      type: "edited",
      file: "domain.yaml",
      text: "changed: again",
    });
    state = workspaceReducer(state, {
      type: "saveFinished",
      requestId: 1,
      revision,
      files: sentFiles,
      response: saveResponse({}),
    });

    expect(state.workspace?.activeFiles["domain.yaml"]).toBe(
      "changed: true",
    );
    expect(state.drafts["domain.yaml"]).toBe("changed: again");
    expect(isDraftDirty(state)).toBe(true);
    // The validation recorded for the saved revision is now stale.
    expect(isValidationStale(state)).toBe(true);
  });

  it("an invalid outcome surfaces the save's diagnostics as validation", () => {
    let state = dirtyState();
    const revision = state.draftRevision;
    state = workspaceReducer(state, { type: "saveStarted", requestId: 1 });
    state = workspaceReducer(state, {
      type: "saveFinished",
      requestId: 1,
      revision,
      files: draftFilesForRequest(state.drafts),
      response: saveResponse({
        outcome: "invalid",
        valid: false,
        digest: "draft-digest",
        diagnostics: [SYNTHETIC_DIAGNOSTIC],
      }),
    });

    expect(state.saveNotice?.outcome).toBe("invalid");
    expect(state.validation?.valid).toBe(false);
    expect(state.validation?.diagnostics).toEqual([SYNTHETIC_DIAGNOSTIC]);
    // Nothing activated: the active layer and draft are untouched.
    expect(state.workspace?.digest).toBe("synthetic-digest");
    expect(isDraftDirty(state)).toBe(true);
  });

  it("a conflict keeps the draft and reports the live digest", () => {
    let state = dirtyState();
    state = workspaceReducer(state, { type: "saveStarted", requestId: 1 });
    state = workspaceReducer(state, {
      type: "saveFinished",
      requestId: 1,
      revision: state.draftRevision,
      files: draftFilesForRequest(state.drafts),
      response: saveResponse({
        outcome: "conflict",
        digest: "on-disk-now",
        message: "workspace files changed on disk",
      }),
    });

    expect(state.saveNotice?.outcome).toBe("conflict");
    expect(state.saveNotice?.digest).toBe("on-disk-now");
    // The draft survives — it is the review object on the reload path.
    expect(state.drafts["domain.yaml"]).toBe("changed: true");
    expect(state.workspace?.digest).toBe("synthetic-digest");
    expect(isDraftDirty(state)).toBe(true);
  });

  it("reloading after a conflict re-bases the draft without losing it", () => {
    let state = dirtyState();
    state = workspaceReducer(state, { type: "saveStarted", requestId: 1 });
    state = workspaceReducer(state, {
      type: "saveFinished",
      requestId: 1,
      revision: state.draftRevision,
      files: draftFilesForRequest(state.drafts),
      response: saveResponse({ outcome: "conflict", digest: "d2" }),
    });

    state = workspaceReducer(state, { type: "reloadStarted" });
    expect(state.reloading).toBe(true);
    const reloadedResponse = {
      ...SYNTHETIC_OPEN_RESPONSE,
      workspace: { ...SYNTHETIC_OPEN_RESPONSE.workspace, digest: "d2" },
      files: {
        ...SYNTHETIC_OPEN_RESPONSE.files,
        "agent.yaml": "# edited outside the UI\n",
      },
    };
    state = workspaceReducer(state, {
      type: "reloaded",
      requestedPath: "job-invoicing",
      response: reloadedResponse,
    });

    expect(state.reloading).toBe(false);
    expect(state.workspace?.digest).toBe("d2");
    expect(state.workspace?.activeFiles["agent.yaml"]).toBe(
      "# edited outside the UI\n",
    );
    // The draft is preserved; the next save runs against digest d2.
    expect(state.drafts["domain.yaml"]).toBe("changed: true");
    expect(isDraftDirty(state)).toBe(true);
    expect(state.saveNotice).toBeNull();
  });

  it("a failed save is a retryable notice; transport errors stay out of diagnostics", () => {
    let state = dirtyState();
    state = workspaceReducer(state, { type: "saveStarted", requestId: 1 });
    state = workspaceReducer(state, {
      type: "saveFinished",
      requestId: 1,
      revision: state.draftRevision,
      files: draftFilesForRequest(state.drafts),
      response: saveResponse({
        outcome: "failed",
        digest: null,
        message: "disk full",
      }),
    });
    expect(state.saveNotice?.outcome).toBe("failed");
    expect(state.saveNotice?.message).toBe("disk full");
    expect(isDraftDirty(state)).toBe(true);

    state = workspaceReducer(state, { type: "saveStarted", requestId: 2 });
    state = workspaceReducer(state, {
      type: "saveFailed",
      requestId: 2,
      error: new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    });
    expect(state.saving).toBe(false);
    expect(state.saveError?.code).toBe("WEB.API_UNREACHABLE");
    expect(state.saveNotice).toBeNull();
  });

  it("a late reload response cannot overwrite a different workspace", () => {
    // The reload was issued against job-invoicing; the user opened
    // other-workspace before it landed. The stale response must not
    // replace the new workspace's active files.
    let state = dirtyState();
    state = workspaceReducer(state, { type: "reloadStarted" });
    state = workspaceReducer(state, {
      type: "opened",
      requestedPath: "other-workspace",
      response: {
        ...SYNTHETIC_OPEN_RESPONSE,
        workspace: {
          ...SYNTHETIC_OPEN_RESPONSE.workspace,
          digest: "other-digest",
        },
        files: {
          ...SYNTHETIC_OPEN_RESPONSE.files,
          "domain.yaml": "other: true",
        },
      },
    });
    state = workspaceReducer(state, {
      type: "reloaded",
      requestedPath: "job-invoicing",
      response: SYNTHETIC_OPEN_RESPONSE,
    });

    expect(state.workspace?.requestedPath).toBe("other-workspace");
    expect(state.workspace?.digest).toBe("other-digest");
    expect(state.workspace?.activeFiles["domain.yaml"]).toBe("other: true");
    expect(state.reloading).toBe(false);
  });

  it("a late reload failure cannot surface on a different workspace", () => {
    let state = dirtyState();
    state = workspaceReducer(state, { type: "reloadStarted" });
    state = workspaceReducer(state, {
      type: "opened",
      requestedPath: "other-workspace",
      response: SYNTHETIC_OPEN_RESPONSE,
    });
    state = workspaceReducer(state, {
      type: "reloadFailed",
      requestedPath: "job-invoicing",
      error: new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    });

    expect(state.workspace?.requestedPath).toBe("other-workspace");
    expect(state.saveError).toBeNull();
    expect(state.reloading).toBe(false);
  });

  it("a reload transport failure lands on the save error surface", () => {
    let state = dirtyState();
    state = workspaceReducer(state, { type: "reloadStarted" });
    state = workspaceReducer(state, {
      type: "reloadFailed",
      requestedPath: "job-invoicing",
      error: new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    });
    expect(state.reloading).toBe(false);
    expect(state.saveError?.code).toBe("WEB.API_UNREACHABLE");
    expect(state.drafts["domain.yaml"]).toBe("changed: true");
  });

  it("a superseded save response cannot roll the active baseline back", () => {
    let state = dirtyState();
    const sentFiles = draftFilesForRequest(state.drafts);
    const revision = state.draftRevision;
    // A second save superseding the first (e.g. a retried request) makes
    // the earlier response stale; it must be discarded, never applied.
    state = workspaceReducer(state, { type: "saveStarted", requestId: 1 });
    state = workspaceReducer(state, { type: "saveStarted", requestId: 2 });
    state = workspaceReducer(state, {
      type: "saveFinished",
      requestId: 1,
      revision,
      files: sentFiles,
      response: saveResponse({ digest: "stale-digest" }),
    });

    expect(state.saving).toBe(true);
    expect(state.workspace?.digest).toBe("synthetic-digest");
    expect(state.workspace?.activeFiles["domain.yaml"]).not.toBe(
      "changed: true",
    );
    expect(state.saveNotice).toBeNull();

    state = workspaceReducer(state, {
      type: "saveFinished",
      requestId: 2,
      revision,
      files: sentFiles,
      response: saveResponse({}),
    });
    expect(state.workspace?.digest).toBe("saved-digest");
    expect(state.saving).toBe(false);
  });

  it("a stale save transport failure is discarded too", () => {
    let state = dirtyState();
    state = workspaceReducer(state, { type: "saveStarted", requestId: 1 });
    state = workspaceReducer(state, { type: "saveStarted", requestId: 2 });
    state = workspaceReducer(state, {
      type: "saveFailed",
      requestId: 1,
      error: new ApiError("WEB.API_UNREACHABLE", "down", null, true),
    });

    expect(state.saving).toBe(true);
    expect(state.saveError).toBeNull();
  });
});
