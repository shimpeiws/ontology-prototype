"""Typed request/response models for the v1 API contract.

Everything here is transport shape only: diagnostics are the core's
``Diagnostic`` objects serialized as-is, and definitions are the core's
typed workspace model serialized field-for-field.
"""

from __future__ import annotations

from typing import Any

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
