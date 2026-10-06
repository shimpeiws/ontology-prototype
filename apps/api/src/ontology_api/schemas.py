"""Typed request/response models for the v1 API contract.

Everything here is transport shape only: diagnostics are the core's
``Diagnostic`` objects serialized as-is, and definitions are the core's
typed workspace model serialized field-for-field.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"


class DiagnosticOut(BaseModel):
    """One core diagnostic: severity, stable code, file, dotted path, hint."""

    severity: str
    code: str
    message: str
    file: str | None = None
    path: str | None = None
    line: int | None = None
    hint: str | None = None


class OpenRequest(BaseModel):
    """Open the workspace at ``path``, which must sit under a configured root."""

    path: str = Field(min_length=1)


class DraftValidateRequest(BaseModel):
    """Validate a draft bundle without touching the workspace on disk.

    ``path`` identifies the workspace the draft belongs to and must resolve
    under a configured root. ``files`` maps the four workspace file names
    to their draft text; omitted files are reported as missing.
    """

    path: str = Field(min_length=1)
    files: dict[str, str]


class SaveRequest(BaseModel):
    """Activate a draft bundle as the workspace on disk.

    ``base_digest`` is the digest the open/inspect response returned —
    the save proceeds only when the on-disk bundle still matches it, so a
    workspace edited outside the UI (e.g. by the CLI) can never be
    silently overwritten. An empty base digest can never match an honest
    open: it is rejected at the boundary rather than weakening the check.
    """

    path: str = Field(min_length=1)
    base_digest: str = Field(min_length=1)
    files: dict[str, str]


class WorkspaceRef(BaseModel):
    """``digest`` is null when the on-disk bundle could not be fully
    represented in ``files`` (e.g. a symlink escaping the roots), so the
    field never reports a hash over a silent subset."""

    path: str
    digest: str | None


class OpenResponse(BaseModel):
    api_version: str
    valid: bool
    workspace: WorkspaceRef
    files: dict[str, str]
    definitions: dict[str, Any] | None
    diagnostics: list[DiagnosticOut]


class DraftValidateResponse(BaseModel):
    api_version: str
    valid: bool
    digest: str
    definitions: dict[str, Any] | None
    diagnostics: list[DiagnosticOut]


class SaveResponse(BaseModel):
    """Result of a save attempt — a domain outcome, not a transport error.

    ``outcome`` is one of ``saved`` / ``invalid`` / ``conflict`` /
    ``failed``, mirroring ``ontology_core.save_workspace``. ``digest`` is
    the new active digest on ``saved``, the current on-disk digest on
    ``conflict`` (the base a reloaded client must present), the draft
    digest on ``invalid``, and null on ``failed``. ``diagnostics`` carry
    core validation findings on ``invalid``; ``message`` carries the
    failure detail on ``failed``.
    """

    api_version: str
    outcome: Literal["saved", "invalid", "conflict", "failed"]
    valid: bool
    digest: str | None
    definitions: dict[str, Any] | None
    diagnostics: list[DiagnosticOut]
    message: str | None = None


class ContractResponse(BaseModel):
    api_version: str
    core_version: str
    schema_version: int
    workspace_files: dict[str, str]
    diagnostic_fields: list[str]
    operations: dict[str, str]
    contract: dict[str, Any]


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
