import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import { api, ApiError } from "../lib/api";
import { useWorkspace } from "../workspace/context";
import { ErrorState, LoadingState } from "../components/states";

export function meta() {
  return [{ title: "ontology — open workspace" }];
}

export default function Home() {
  const { state, open } = useWorkspace();
  const navigate = useNavigate();
  const [path, setPath] = useState("job-invoicing");
  const [pathError, setPathError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // The empty-path guard lives here (the single entry point for both the
  // form submit and the error retry) so neither path can reach the API
  // with an empty workspace path.
  async function doOpen(target: string) {
    const trimmed = target.trim();
    setError(null);
    if (!trimmed) {
      setPathError("Enter a workspace path.");
      return;
    }
    setPathError(null);
    setOpening(true);
    try {
      const response = await api.openWorkspace(trimmed);
      open(trimmed, response);
      navigate("/workspace");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err
          : new ApiError("WEB.UNEXPECTED", String(err), null, false),
      );
    } finally {
      setOpening(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void doOpen(path);
  }

  return (
    <main className="home">
      <h1>FDE workspace</h1>
      <p>
        Open a workspace directory under the API's configured roots. Paths may
        be absolute or relative to a configured root — the API resolves them.
      </p>

      {state.workspace ? (
        <p>
          Workspace <code>{state.workspace.resolvedPath}</code> is open.{" "}
          <Link to="/workspace">Go to the overview</Link>, or open a different
          workspace below.
        </p>
      ) : null}

      <form onSubmit={submit} className="open-form" noValidate>
        <label htmlFor="workspace-path">Workspace path</label>
        <input
          id="workspace-path"
          name="workspace-path"
          type="text"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          aria-describedby="workspace-path-hint"
          aria-invalid={pathError !== null}
          autoFocus
        />
        <p id="workspace-path-hint" className="hint">
          e.g. <code>job-invoicing</code> when the API runs with
          <code> --root examples</code>
        </p>
        {pathError ? (
          <p className="field-error" role="alert">
            {pathError}
          </p>
        ) : null}
        <button type="submit" disabled={opening}>
          Open workspace
        </button>
      </form>

      {opening ? <LoadingState label="Opening workspace…" /> : null}
      {error ? (
        <ErrorState error={error} onRetry={() => void doOpen(path)} />
      ) : null}
    </main>
  );
}
