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
});
