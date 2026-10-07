# ADR 0001: Use React Router Framework Mode for the Web app

- Status: Accepted
- Date: 2026-10-06
- Decision owner: repository maintainer

## Context

The v0.2 Web app edits four YAML workspace definitions through the Python API. The shared core remains the authority for validation and persistence. The Web app must not read SQLite or operational storage directly. The UI is a local authoring tool, so public search indexing and server-rendered pages are not requirements.

The framework comparison considered React Router Framework Mode, TanStack Start, and Next.js. The API comparison considered FastAPI and Flask. Hono was considered and excluded because it is a TypeScript API framework, not a React framework, and would add a TypeScript service in front of the Python core.

## Decision

Use React Router v7 Framework Mode for `apps/web`, configured for SPA output unless a later requirement needs runtime server rendering. The browser calls the Python API. React Router owns navigation, route data state, pending and error UI, and client-side draft state. The Python API calls `ontology_core` for authoritative validation and workspace operations.

Use FastAPI for `apps/api`. It fits the Python core and provides typed request validation and OpenAPI output. The maintainer approved FastAPI on 2026-10-06.

## Alternatives

### TanStack Start

TanStack Start offers type-safe routing, search parameters, loaders, and server functions. Its router model is attractive for an editor with state reflected in URLs. Its server functions would duplicate an application-server layer when the Python API already owns that boundary. The project also marks TanStack Start as a release candidate, so its APIs may change more than React Router's.

### Next.js

Next.js has the broadest adoption and combines React UI with server routes. Keeping the Python core would still require a Python API process. Adding Next.js server routes as a proxy would add another layer. Replacing the Python API would require moving core behavior to TypeScript or calling the CLI as a subprocess, which conflicts with the existing boundary.

### FastAPI and Flask for the API

FastAPI maps Python type declarations to validated request models and OpenAPI documentation. Flask is smaller and flexible, but the API contract and request validation need separate choices. Both can call the Python core directly. The maintainer selected FastAPI because its typed request validation and OpenAPI support fit the planned API contract.

## Consequences

- The Web app uses React Router Framework Mode and can build a static SPA with `ssr: false`.
- The Web app and API have separate development processes. Vite proxies `/api` to the API during development.
- Route loaders and client actions call the API. They do not reimplement core validation.
- TanStack Start's stronger typed URL model is deferred. It can be reconsidered if route search state becomes a central product requirement.
- The API and Web choices are recorded before framework-dependent implementation begins.

## Proposed local commands

These commands describe the planned packages. They become executable when `apps/api` and `apps/web` exist and define the referenced scripts.

```bash
python -m pip install -e "packages/core[test]" -e "apps/cli[test]" -e "apps/api[test]"
python -m uvicorn ontology_api.main:app --reload --host 127.0.0.1 --port 8000
npm --prefix apps/web ci
npm --prefix apps/web run dev -- --host 127.0.0.1
python -m pytest
npm --prefix apps/web run typecheck
npm --prefix apps/web run test
npm --prefix apps/web run build
```

## Ownership and access boundaries

- `packages/core` owns workspace contracts, validation, and persistence semantics. It imports no app code.
- `apps/api` owns HTTP request and response handling, calls the shared core, and restricts workspace access to configured roots.
- `apps/web` owns presentation, navigation, and draft state. It reaches workspace files only through the API.
- `apps/cli` continues to use the same core and workspace files.
- Local servers bind to `127.0.0.1`. The API resolves requested paths and verifies that the resolved path remains under a configured root, including when symlinks are present.
- The API and Web authoring flow does not access canonical SQLite data, traces, proposals, approvals, or write-back storage. The boundary for the operational surface — inspection reads and decision writes over the same store, with execution never exposed — is decided separately in [ADR 0003](0003-api-operational-boundary.md).
