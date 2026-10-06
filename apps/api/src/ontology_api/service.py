"""Workspace service: the only path from HTTP into the shared core.

``CoreWorkspaceService`` resolves client-supplied paths against the
configured roots (after symlink resolution) and delegates every workspace
operation to ``ontology_core`` — the API never re-validates and never
runs the CLI. ``WorkspaceService`` is a protocol so tests and embeddings
can inject a different implementation without touching the transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Protocol

import ontology_core
from ontology_core import (
    WORKSPACE_FILES,
    Diagnostic,
    Workspace,
    bundle_digest,
    load_workspace,
    load_workspace_texts,
)
from ontology_core.agentdef import DELIVERABLES
from ontology_core.conditions import COMPARISON_OPS, LOGIC_OPS, NULL_OPS, SET_OPS
from ontology_core.evaluation import (
    AGGREGATIONS,
    COMPARATORS,
    GUARDRAIL_COMPARATORS,
    METRICS,
    UNITS,
)
from ontology_core.mapping import SOURCE_FORMATS, TIME_CASTS
from ontology_core.model import CAST_TYPES, PROPERTY_TYPES, SCALAR_TYPES
from ontology_core.preview import (
    MAX_PREVIEW_BYTES,
    MAX_PREVIEW_RECORDS,
    preview_snapshot,
)
from ontology_core.workspace import SUPPORTED_SCHEMA_VERSION
from ontology_core.yamlio import MAX_FILE_BYTES

from .schemas import API_VERSION


class ApiError(Exception):
    """A transport-level failure: HTTP status plus a stable code.

    API errors are never reported inside the diagnostics array — the
    diagnostics channel carries core validation findings only, so the
    frontend can tell a failed request apart from an invalid draft.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass
class OpenResult:
    """Outcome of opening a workspace directory under a configured root."""

    path: Path
    valid: bool
    diagnostics: list[Diagnostic]
    files: dict[str, str]
    digest: str | None
    definitions: dict[str, Any] | None


@dataclass
class DraftResult:
    """Outcome of validating a draft bundle — pure, no side effects."""

    path: Path
    valid: bool
    diagnostics: list[Diagnostic]
    digest: str
    definitions: dict[str, Any] | None


@dataclass
class DraftPreviewResult:
    """Outcome of a draft preview — isolated, nothing persisted.

    ``preview`` carries the core's preview payload (input/mapping digests,
    per-record observations with diagnostics) and is ``None`` when the draft
    bundle did not validate into a typed workspace.
    """

    path: Path
    valid: bool
    diagnostics: list[Diagnostic]
    digest: str
    preview: dict[str, Any] | None


class WorkspaceService(Protocol):
    """Workspace operations the API exposes. Injectable for tests."""

    def open(self, path: str) -> OpenResult: ...

    def validate_draft(
        self, path: str, files: Mapping[str, str]
    ) -> DraftResult: ...

    def preview_draft(
        self, path: str, files: Mapping[str, str], sample_text: str
    ) -> DraftPreviewResult: ...


class CoreWorkspaceService:
    """WorkspaceService over ``ontology_core``, scoped to configured roots."""

    def __init__(self, roots: tuple[str | Path, ...] | list[str | Path]) -> None:
        self._roots = tuple(Path(r).expanduser().resolve() for r in roots)

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def open(self, path: str) -> OpenResult:
        resolved = self._resolve(path)
        try:
            is_dir = resolved.is_dir()
        except OSError:
            is_dir = False
        if not is_dir:
            raise ApiError(
                404,
                "API.WORKSPACE_NOT_FOUND",
                f"no workspace directory at {path!r} (resolved: {resolved})",
            )
        result = load_workspace(resolved)
        files, omitted = self._read_files(resolved)
        return OpenResult(
            path=resolved,
            valid=result.ok,
            diagnostics=result.diagnostics,
            files=files,
            # The digest must describe the on-disk bundle the client is
            # about to edit; when part of it cannot be returned (an
            # escaping symlink, undecodable or oversized file) there is
            # no honest digest, so the field is null instead of a hash
            # over a silent subset.
            digest=bundle_digest(files) if not omitted else None,
            definitions=_definitions(result.workspace),
        )

    def validate_draft(self, path: str, files: Mapping[str, str]) -> DraftResult:
        resolved = self._resolve(path)
        self._check_file_names(files)
        result = load_workspace_texts(files, path=resolved)
        return DraftResult(
            path=resolved,
            valid=result.ok,
            diagnostics=result.diagnostics,
            digest=bundle_digest(files),
            definitions=_definitions(result.workspace),
        )

    def preview_draft(
        self, path: str, files: Mapping[str, str], sample_text: str
    ) -> DraftPreviewResult:
        """Resolve a draft mapping against an in-memory sample.

        The draft bundle is validated exactly like ``validate_draft``; only a
        valid bundle produces a ``preview`` payload. The core preview reads
        nothing from disk and writes nothing anywhere — no workspace files,
        no ``.ontology/`` runtime artifacts, no canonical store.
        """
        resolved = self._resolve(path)
        self._check_file_names(files)
        result = load_workspace_texts(files, path=resolved)
        preview = (
            _preview_payload(result.workspace, sample_text)
            if result.workspace is not None
            else None
        )
        return DraftPreviewResult(
            path=resolved,
            valid=result.ok,
            diagnostics=result.diagnostics,
            digest=bundle_digest(files),
            preview=preview,
        )

    def _check_file_names(self, files: Mapping[str, str]) -> None:
        unknown = sorted(set(files) - set(WORKSPACE_FILES.values()))
        if unknown:
            raise ApiError(
                400,
                "API.UNKNOWN_WORKSPACE_FILE",
                f"unknown workspace file(s): {', '.join(unknown)}; "
                f"expected only {', '.join(sorted(WORKSPACE_FILES.values()))}",
            )

    def _resolve(self, raw_path: str) -> Path:
        """Resolve a client path and require containment under a root.

        Resolution is the security boundary: ``resolve()`` collapses
        ``..`` segments and follows symlinks, so a symlink inside a root
        pointing outside it is still rejected.
        """
        if not self._roots:
            raise ApiError(
                503,
                "API.ROOTS_UNCONFIGURED",
                "no workspace roots configured; start the server with "
                "--root or the ONTOLOGY_API_ROOTS environment variable",
            )
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            resolved = _resolve(candidate)
        else:
            # Relative paths resolve against the configured roots, never
            # against the server's cwd: the first root containing the
            # path wins, and when no root contains it the first root is
            # used so draft identities still resolve inside a root.
            resolved = None
            for root in self._roots:
                probe = _resolve(root / candidate)
                if (
                    probe is not None
                    and probe.is_dir()
                    and any(probe.is_relative_to(r) for r in self._roots)
                ):
                    resolved = probe
                    break
            if resolved is None:
                resolved = _resolve(self._roots[0] / candidate)
        if resolved is None:
            raise ApiError(
                400,
                "API.INVALID_PATH",
                f"cannot resolve workspace path {raw_path!r}",
            )
        if not any(resolved.is_relative_to(root) for root in self._roots):
            raise ApiError(
                403,
                "API.PATH_OUTSIDE_ROOTS",
                f"workspace path {raw_path!r} is outside the configured roots",
            )
        return resolved

    def _read_files(self, root: Path) -> tuple[dict[str, str], list[str]]:
        """Read workspace files for the inspect payload.

        Returns ``(files, omitted)``: ``files`` holds the byte-exact
        UTF-8 text of each on-disk workspace file (same
        ``read_bytes().decode`` recipe as ``load_yaml_file``, so digests
        computed from it match digests over the draft bundle), and
        ``omitted`` names every file present on disk that could not be
        returned — oversized, undecodable, or a symlink escaping every
        configured root, which is never disclosed.
        """
        files: dict[str, str] = {}
        omitted: list[str] = []
        for filename in WORKSPACE_FILES.values():
            file_path = root / filename
            try:
                if not file_path.is_file():
                    continue
                resolved_file = file_path.resolve()
                if not any(
                    resolved_file.is_relative_to(root_) for root_ in self._roots
                ) or resolved_file.stat().st_size > MAX_FILE_BYTES:
                    omitted.append(filename)
                    continue
                files[filename] = resolved_file.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                omitted.append(filename)
        return files, omitted


def _resolve(candidate: Path) -> Path | None:
    try:
        return candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _definitions(workspace: Workspace | None) -> dict[str, Any] | None:
    if workspace is None:
        return None
    return {
        "domain": asdict(workspace.domain),
        "mapping": asdict(workspace.mapping),
        "evaluation": asdict(workspace.evaluation),
        "agent": asdict(workspace.agent),
    }


def _preview_payload(workspace: Workspace, sample_text: str) -> dict[str, Any]:
    """Run the core preview over the sample text and shape the response.

    ``surrogatepass`` mirrors ``bundle_digest``: a stray surrogate in the
    JSON-decoded string encodes here, then fails the UTF-8 decode inside the
    preview as an ``INGEST.ENCODING_ERROR`` diagnostic — never a crash.
    """
    content = sample_text.encode("utf-8", errors="surrogatepass")
    result = preview_snapshot(workspace, content)
    observed_at = (
        result.observed_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
        if result.observed_at is not None
        else None
    )
    return {
        "preview_id": result.preview_id,
        "source": result.source,
        "format": result.format,
        "input_digest": result.content_digest,
        "mapping_digest": result.mapping_digest,
        "observed_at": observed_at,
        "record_count": result.record_count,
        "truncated": result.truncated,
        "observations": [o.to_dict() for o in result.observations],
        "diagnostics": [d.to_dict() for d in result.diagnostics],
    }


def contract_metadata() -> dict[str, Any]:
    """Versioned contract/schema metadata derived from the shared core.

    Values come straight from the core's authoritative constants so the
    frontend contract can never drift from what validation accepts.
    """
    return {
        "api_version": API_VERSION,
        "core_version": ontology_core.__version__,
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "workspace_files": dict(WORKSPACE_FILES),
        "diagnostic_fields": [
            "severity",
            "code",
            "message",
            "file",
            "path",
            "line",
            "hint",
        ],
        "operations": {
            "open": f"POST /api/{API_VERSION}/workspaces/open",
            "validate_draft": f"POST /api/{API_VERSION}/workspaces/drafts/validate",
            "preview_draft": f"POST /api/{API_VERSION}/workspaces/drafts/preview",
        },
        "contract": {
            "property_types": list(PROPERTY_TYPES),
            "scalar_types": list(SCALAR_TYPES),
            "cast_types": list(CAST_TYPES),
            "time_casts": list(TIME_CASTS),
            "source_formats": list(SOURCE_FORMATS),
            "preview_limits": {
                "max_bytes": MAX_PREVIEW_BYTES,
                "max_records": MAX_PREVIEW_RECORDS,
            },
            "condition_operators": {
                "comparison": sorted(COMPARISON_OPS),
                "set": sorted(SET_OPS),
                "null": sorted(NULL_OPS),
                "logic": sorted(LOGIC_OPS),
            },
            "evaluation": {
                "metrics": list(METRICS),
                "aggregations": list(AGGREGATIONS),
                "units": list(UNITS),
                "comparators": list(COMPARATORS),
                "guardrail_comparators": list(GUARDRAIL_COMPARATORS),
            },
            "deliverables": list(DELIVERABLES),
        },
    }
