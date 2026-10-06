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
import {
  SYNTHETIC_DIAGNOSTIC,
  SYNTHETIC_OPEN_RESPONSE,
} from "./fixtures";

function opened(): WorkspaceState {
  return workspaceReducer(initialWorkspaceState, {
    type: "opened",
    requestedPath: "job-invoicing",
    response: SYNTHETIC_OPEN_RESPONSE,
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
});
