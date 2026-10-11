/**
 * API client tests — synthetic fixture mode: fetch is stubbed; these are
 * NOT real API responses (see fixtures.ts).
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../lib/api";
import {
  SYNTHETIC_OPEN_RESPONSE,
  SYNTHETIC_SAVE_RESPONSE,
  SYNTHETIC_PREVIEW_RESPONSE,
  SYNTHETIC_VALIDATE_RESPONSE,
  stubFetchJson,
} from "./fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("parses a successful open response", async () => {
    const fetchStub = vi.fn(stubFetchJson(200, SYNTHETIC_OPEN_RESPONSE));
    vi.stubGlobal("fetch", fetchStub);

    const result = await api.openWorkspace("job-invoicing");

    expect(result.valid).toBe(true);
    expect(result.workspace.digest).toBe("synthetic-digest");
    expect(fetchStub).toHaveBeenCalledOnce();
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces/open");
    expect(JSON.parse(init.body as string)).toEqual({
      path: "job-invoicing",
    });
  });

  it("sends draft files to the validate endpoint", async () => {
    const fetchStub = vi.fn(stubFetchJson(200, SYNTHETIC_VALIDATE_RESPONSE));
    vi.stubGlobal("fetch", fetchStub);

    const result = await api.validateDraft("job-invoicing", {
      "domain.yaml": "x",
    });

    expect(result.valid).toBe(false);
    expect(result.diagnostics[0].code).toBe("WORKSPACE.EXAMPLE");
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces/drafts/validate");
    expect(JSON.parse(init.body as string)).toEqual({
      path: "job-invoicing",
      files: { "domain.yaml": "x" },
    });
  });

  it("sends path, base digest, and draft files to the save endpoint", async () => {
    const fetchStub = vi.fn(stubFetchJson(200, SYNTHETIC_SAVE_RESPONSE));
    vi.stubGlobal("fetch", fetchStub);

    const result = await api.saveWorkspace("job-invoicing", "digest-abc", {
      "domain.yaml": "x",
    });

    expect(result.outcome).toBe("saved");
    expect(result.digest).toBe("synthetic-saved-digest");
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces/save");
    expect(JSON.parse(init.body as string)).toEqual({
      path: "job-invoicing",
      base_digest: "digest-abc",
      files: { "domain.yaml": "x" },
    });
  });

  it("sends draft files and the sample to the preview endpoint", async () => {
    const fetchStub = vi.fn(stubFetchJson(200, SYNTHETIC_PREVIEW_RESPONSE));
    vi.stubGlobal("fetch", fetchStub);

    const result = await api.previewDraft(
      "job-invoicing",
      { "mapping.yaml": "x" },
      { text: "id,status\n101,done" },
    );

    expect(result.valid).toBe(true);
    expect(result.preview?.preview_id).toBe("prev-aaaabbbbcccc-dddd");
    expect(result.preview?.observations[0].row).toBe(2);
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces/drafts/preview");
    expect(JSON.parse(init.body as string)).toEqual({
      path: "job-invoicing",
      files: { "mapping.yaml": "x" },
      sample: { text: "id,status\n101,done" },
    });
  });

  it("structured error payload becomes ApiError with the server code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetchJson(403, {
          error: {
            code: "API.PATH_OUTSIDE_ROOTS",
            message: "outside the configured roots",
          },
        }),
      ),
    );

    const err = await api.openWorkspace("/etc").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("API.PATH_OUTSIDE_ROOTS");
    expect((err as ApiError).status).toBe(403);
    expect((err as ApiError).retryable).toBe(false);
  });

  it("network failure becomes a retryable WEB.API_UNREACHABLE", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("fetch failed"))),
    );

    const err = await api.openWorkspace("x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("WEB.API_UNREACHABLE");
    expect((err as ApiError).retryable).toBe(true);
  });

  it("non-JSON response becomes WEB.BAD_RESPONSE", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response("<html>oops</html>", {
            status: 500,
            headers: { "Content-Type": "text/html" },
          }),
        ),
      ),
    );

    const err = await api.openWorkspace("x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("WEB.BAD_RESPONSE");
    expect((err as ApiError).retryable).toBe(true);
  });

  it("error body detail is preserved on the ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetchJson(422, {
          error: {
            code: "API.MALFORMED_REQUEST",
            message: "invalid body",
            detail: { field: "path" },
          },
        }),
      ),
    );

    const err = await api.openWorkspace("x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).detail).toEqual({ field: "path" });
  });

  it("HTTP error without structured body keeps the raw payload as detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(stubFetchJson(404, { detail: "not found" })),
    );

    const err = await api.openWorkspace("x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("WEB.BAD_RESPONSE");
    expect((err as ApiError).detail).toEqual({ detail: "not found" });
  });

  it("HTTP error without structured body stays out of diagnostics shape", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(stubFetchJson(404, { detail: "not found" })),
    );

    const err = await api.openWorkspace("x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("WEB.BAD_RESPONSE");
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).retryable).toBe(false);
  });

  /* Loop-stage triggers (ADR 0004): each call posts the CLI-shaped
   * request to its own route; a 200 `ok:false` body is a domain
   * outcome, not an exception. */

  it("posts the snapshot to the ingest trigger", async () => {
    const fetchStub = vi.fn(
      stubFetchJson(200, {
        api_version: "v1",
        workspace: "job-invoicing",
        snapshot: "jobs.csv",
        ok: true,
        ingest_id: "ing-x",
        diagnostics: [],
      }),
    );
    vi.stubGlobal("fetch", fetchStub);

    const result = await api.ingest("job-invoicing", {
      text: "id,status\n101,done",
      name: "jobs.csv",
      source: "jobs",
    });
    expect(result.ok).toBe(true);
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces/ingest");
    expect(JSON.parse(init.body as string)).toEqual({
      path: "job-invoicing",
      snapshot: {
        text: "id,status\n101,done",
        name: "jobs.csv",
        source: "jobs",
      },
    });
  });

  it("posts the projection trigger with as-of and projection", async () => {
    const fetchStub = vi.fn(
      stubFetchJson(200, {
        api_version: "v1",
        workspace: "job-invoicing",
        ok: true,
        run_id: "prj-x",
        candidates: [],
        diagnostics: [],
      }),
    );
    vi.stubGlobal("fetch", fetchStub);

    await api.project("job-invoicing", "2026-10-21T00:00:00Z", "MyProj");
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces/project");
    expect(JSON.parse(init.body as string)).toEqual({
      path: "job-invoicing",
      as_of: "2026-10-21T00:00:00Z",
      projection: "MyProj",
    });
  });

  it("posts the diagnosis trigger with run_id source and provider", async () => {
    const fetchStub = vi.fn(
      stubFetchJson(200, {
        api_version: "v1",
        workspace: "job-invoicing",
        ok: true,
        run_id: "dgx-x",
        diagnostics: [],
      }),
    );
    vi.stubGlobal("fetch", fetchStub);

    await api.diagnose(
      "job-invoicing",
      { runId: "prj-x" },
      { provider: "stub", model: "m1" },
    );
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces/diagnose");
    expect(JSON.parse(init.body as string)).toEqual({
      path: "job-invoicing",
      run_id: "prj-x",
      as_of: null,
      provider: "stub",
      model: "m1",
    });
  });

  it("posts the evaluation trigger with boundary and as-of", async () => {
    const fetchStub = vi.fn(
      stubFetchJson(200, {
        api_version: "v1",
        workspace: "job-invoicing",
        ok: true,
        run_id: "evl-x",
        diagnostics: [],
      }),
    );
    vi.stubGlobal("fetch", fetchStub);

    await api.evaluate(
      "job-invoicing",
      "2026-10-15T00:00:00Z",
      "2026-10-26T00:00:00Z",
    );
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces/evaluate");
    expect(JSON.parse(init.body as string)).toEqual({
      path: "job-invoicing",
      boundary: "2026-10-15T00:00:00Z",
      as_of: "2026-10-26T00:00:00Z",
    });
  });

  it("posts the proposal id to the execute trigger", async () => {
    const fetchStub = vi.fn(
      stubFetchJson(200, {
        api_version: "v1",
        workspace: "job-invoicing",
        ok: false,
        diagnostics: [
          {
            severity: "error",
            code: "DECISION.NOT_APPROVED",
            message: "proposal is not approved",
            file: null,
            path: null,
            line: null,
            hint: null,
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchStub);

    // A refusal is a 200 domain payload, not an exception.
    const result = await api.execute("job-invoicing", "dgx-x-p0");
    expect(result.ok).toBe(false);
    expect(result.diagnostics?.[0].code).toBe("DECISION.NOT_APPROVED");
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/workspaces/execute");
    expect(JSON.parse(init.body as string)).toEqual({
      path: "job-invoicing",
      proposal_id: "dgx-x-p0",
    });
  });
});
