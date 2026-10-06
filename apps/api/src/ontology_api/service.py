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
from pathlib import Path
from typing import Any, Protocol

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
from ontology_core.mapping import SOURCE_FORMATS
from ontology_core.model import CAST_TYPES, PROPERTY_TYPES, SCALAR_TYPES
from ontology_core.workspace import SUPPORTED_SCHEMA_VERSION
from ontology_core.yamlio import MAX_FILE_BYTES


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
    digest: str
    definitions: dict[str, Any] | None


@dataclass
class DraftResult:
    """Outcome of validating a draft bundle — pure, no side effects."""

    path: Path
    valid: bool
    diagnostics: list[Diagnostic]
    digest: str
    definitions: dict[str, Any] | None


class WorkspaceService(Protocol):
    """Workspace operations the API exposes. Injectable for tests."""

    def open(self, path: str) -> OpenResult: ...

    def validate_draft(
        self, path: str, files: Mapping[str, str]
    ) -> DraftResult: ...


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
        files = self._read_files(resolved)
        return OpenResult(
            path=resolved,
            valid=result.ok,
            diagnostics=result.diagnostics,
            files=files,
            digest=bundle_digest(files),
            definitions=_definitions(result.workspace),
        )

    def validate_draft(self, path: str, files: Mapping[str, str]) -> DraftResult:
        resolved = self._resolve(path)
        unknown = sorted(set(files) - set(WORKSPACE_FILES.values()))
        if unknown:
            raise ApiError(
                400,
                "API.UNKNOWN_WORKSPACE_FILE",
                f"unknown workspace file(s): {', '.join(unknown)}; "
                f"expected only {', '.join(sorted(WORKSPACE_FILES.values()))}",
            )
        result = load_workspace_texts(files, path=resolved)
        return DraftResult(
            path=resolved,
            valid=result.ok,
            diagnostics=result.diagnostics,
            digest=bundle_digest(files),
            definitions=_definitions(result.workspace),
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
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ApiError(
                400,
                "API.INVALID_PATH",
                f"cannot resolve workspace path {raw_path!r}: {exc}",
            ) from exc
        if not any(resolved.is_relative_to(root) for root in self._roots):
            raise ApiError(
                403,
                "API.PATH_OUTSIDE_ROOTS",
                f"workspace path {raw_path!r} is outside the configured roots",
            )
        return resolved

    def _read_files(self, root: Path) -> dict[str, str]:
        """Read workspace files for the inspect payload.

        Only UTF-8-decodable files within the size cap are returned — the
        core diagnostics already report the rest. A file that is itself a
        symlink escaping every configured root is never disclosed.
        """
        files: dict[str, str] = {}
        for filename in WORKSPACE_FILES.values():
            file_path = root / filename
            try:
                resolved_file = file_path.resolve()
                if not any(
                    resolved_file.is_relative_to(root_) for root_ in self._roots
                ):
                    continue
                if resolved_file.stat().st_size > MAX_FILE_BYTES:
                    continue
                files[filename] = resolved_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return files


def _definitions(workspace: Workspace | None) -> dict[str, Any] | None:
    if workspace is None:
        return None
    return {
        "domain": asdict(workspace.domain),
        "mapping": asdict(workspace.mapping),
        "evaluation": asdict(workspace.evaluation),
        "agent": asdict(workspace.agent),
    }


def contract_metadata() -> dict[str, Any]:
    """Versioned contract/schema metadata derived from the shared core.

    Values come straight from the core's authoritative constants so the
    frontend contract can never drift from what validation accepts.
    """
    import ontology_core

    return {
        "api_version": "v1",
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
            "open": "POST /api/v1/workspaces/open",
            "validate_draft": "POST /api/v1/workspaces/drafts/validate",
        },
        "contract": {
            "property_types": list(PROPERTY_TYPES),
            "scalar_types": list(SCALAR_TYPES),
            "cast_types": list(CAST_TYPES),
            "source_formats": list(SOURCE_FORMATS),
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
