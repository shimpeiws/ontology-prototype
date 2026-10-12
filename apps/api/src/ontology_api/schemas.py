"""Typed request/response models for the v1 API contract.

Everything here is transport shape only: diagnostics are the core's
``Diagnostic`` objects serialized as-is, and definitions are the core's
typed workspace model serialized field-for-field.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class PreviewSample(BaseModel):
    """A bounded source sample: raw CSV/JSON text, format decided by the
    selected mapping source's ``format`` — never by the request.

    ``source`` names the declared source the sample resolves through;
    omitted means the draft's single source. An unknown or ambiguous name
    is a preview diagnostic, not a guess.
    """

    text: str
    source: str | None = None


class DraftPreviewRequest(BaseModel):
    """Preview a draft mapping against an in-memory sample.

    Same draft semantics as ``DraftValidateRequest``; ``sample.text`` is the
    raw CSV/JSON content the draft mapping is resolved against and
    ``sample.source`` selects the declared source when the draft declares
    several. Nothing is persisted — the request is pure like draft
    validation.
    """

    path: str = Field(min_length=1)
    files: dict[str, str]
    sample: PreviewSample


class WorkspacePathRequest(BaseModel):
    """Identify a workspace directory under the configured roots."""

    path: str = Field(min_length=1)


class TraceSubjectRequest(WorkspacePathRequest):
    """Follow one subject through the recorded run — like the CLI's
    ``trace --subject Entity/Id``."""

    # Ingestion stores ids verbatim with no length cap, so a request cap
    # would reject valid stored subjects; the echo into diagnostic
    # messages is bounded in the service instead.
    subject: str = Field(min_length=1)


class TraceRunRequest(WorkspacePathRequest):
    """Inspect one recorded artifact id — like the CLI's ``trace --run``."""

    run_id: str = Field(min_length=1, max_length=1024)


class EvaluationRunRequest(WorkspacePathRequest):
    """Read one frozen evaluation report — like the CLI's
    ``evaluate --run``."""

    run_id: str = Field(min_length=1, max_length=1024)


class ProposalRequest(WorkspacePathRequest):
    """Inspect one persisted proposal — like the CLI's
    ``proposals --show``."""

    proposal_id: str = Field(min_length=1, max_length=1024)


class DiagnosisRunShowRequest(WorkspacePathRequest):
    """Read back a recorded diagnosis run — like the CLI's
    ``diagnose --inspect``."""

    run_id: str = Field(min_length=1, max_length=1024)


class IngestSnapshot(BaseModel):
    """A snapshot submitted as text — the console's equivalent of the
    snapshot file the CLI reads. ``name`` is an optional label echoed in
    diagnostics (sanitized to a basename by the service); ``source``
    selects the declared source, required when the mapping declares
    several. The format is always the selected source's declared format —
    never request-controlled (ADR 0004)."""

    text: str
    name: str | None = Field(default=None, max_length=512)
    source: str | None = Field(default=None, max_length=1024)


class IngestRequest(WorkspacePathRequest):
    """Run one ingest from the console — the CLI's
    ``ingest <workspace> <snapshot> --json``."""

    snapshot: IngestSnapshot


class ProjectRunRequest(WorkspacePathRequest):
    """Compute a projection from the console — the CLI's
    ``project --as-of --json``.

    ``as_of`` is optional at the schema layer so a missing value fails
    the same way the CLI's missing ``--as-of`` does — a
    ``PROJECTION.AS_OF_REQUIRED`` domain diagnostic, not a 422.
    """

    as_of: str | None = Field(default=None, max_length=1024)
    projection: str | None = Field(default=None, max_length=1024)


class DiagnoseRunRequest(WorkspacePathRequest):
    """Run a diagnosis from the console — the CLI's
    ``diagnose --run|--as-of --provider --json``.

    ``provider`` defaults to the CLI's default (``openrouter``); the
    console offers ``stub`` as the credential-free choice. ``model``
    follows the CLI's env/default chain when omitted. Exactly one of
    ``run_id``/``as_of`` must be set — checked in the service so the
    conflict is the CLI's ``DIAGNOSIS.*`` diagnostic, not a 422.
    """

    run_id: str | None = Field(default=None, max_length=1024)
    as_of: str | None = Field(default=None, max_length=1024)
    provider: str = Field(default="openrouter", min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=1024)


class EvaluateRunTriggerRequest(WorkspacePathRequest):
    """Compute an evaluation from the console — the CLI's
    ``evaluate --boundary --as-of --json``.

    Both times are optional at the schema layer so a missing value fails
    with the CLI's ``EVALUATION.REQUIRED_ARGS`` diagnostic, not a 422.
    """

    boundary: str | None = Field(default=None, max_length=1024)
    as_of: str | None = Field(default=None, max_length=1024)


class ExecuteRequest(WorkspacePathRequest):
    """Trigger write-back of an approved proposal — the CLI's
    ``execute <workspace> <proposal> --json`` (ADR 0004)."""

    proposal_id: str = Field(min_length=1, max_length=1024)


class DecideRequest(WorkspacePathRequest):
    """Record a human decision on a persisted proposal — the CLI's
    ``decide --approve/--reject --reviewer``.

    ``decision`` goes to the core verbatim: an invalid value is a domain
    diagnostic (``DECISION.INVALID_DECISION``), not a schema rejection.
    ``reviewer`` is required and self-asserted (ADR 0003) — a
    whitespace-only value slips past ``min_length`` and fails in the
    core as ``DECISION.NO_REVIEWER``, same as the CLI.
    """

    proposal_id: str = Field(min_length=1, max_length=1024)
    decision: str = Field(min_length=1, max_length=1024)
    reviewer: str = Field(min_length=1, max_length=1024)
    reason: str | None = Field(default=None, max_length=4096)


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


class DraftPreviewResponse(BaseModel):
    """Preview outcome: draft diagnostics plus the isolated run payload.

    ``preview`` is null when the draft bundle did not validate (there is no
    typed workspace to resolve against); the ``diagnostics`` explain why.
    """

    api_version: str
    valid: bool
    digest: str
    diagnostics: list[DiagnosticOut]
    preview: dict[str, Any] | None


class TraceOverviewResponse(BaseModel):
    """The ``ontology trace --json`` overview payload plus ``api_version``.

    The section fields are the core's overview dict verbatim; ``extra``
    keys pass through so a new core section can never be silently dropped
    and diverge from the CLI payload. Every section is optional so a
    degraded assembly can never trip response validation into a 500 —
    whatever the core emits is what the client sees.
    """

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    journal: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    canonical: dict[str, Any] | None = None
    projection_runs: list[dict[str, Any]] | None = None
    diagnosis_runs: list[dict[str, Any]] | None = None
    proposals: dict[str, Any] | None = None
    decisions: int | None = None
    executions: dict[str, Any] | None = None
    evaluation_runs: list[dict[str, Any]] | None = None
    links: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class TraceViewResponse(BaseModel):
    """The ``trace --subject``/``trace --run`` JSON payload plus
    ``api_version``. ``trace`` is omitted on domain failures — the CLI
    emits only ``workspace``/``ok``/``diagnostics`` there too."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    trace: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class EvaluationRunsResponse(BaseModel):
    """Frozen evaluation run list. On a store failure ``evaluation_runs``
    is omitted and ``diagnostics`` carries ``EVALUATION.STORE_ERROR``,
    mirroring the CLI's error payload."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    evaluation_runs: list[dict[str, Any]] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class EvaluationRunResponse(BaseModel):
    """The ``evaluate --run`` JSON payload plus ``api_version``: ``run``
    on success, ``diagnostics`` on a domain failure."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    run: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class ProposalsResponse(BaseModel):
    """The ``proposals --json`` payload plus ``api_version``: ``proposals``
    on success, ``diagnostics`` when the workspace itself is invalid."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    proposals: list[dict[str, Any]] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class ProposalResponse(BaseModel):
    """The ``proposals --show`` JSON payload plus ``api_version``:
    ``proposal`` on success, ``diagnostics`` on a domain failure."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    proposal: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class DiagnosisRunShowResponse(BaseModel):
    """The ``diagnose --inspect`` JSON payload plus ``api_version``:
    ``run`` on success, ``diagnostics`` on a domain failure."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    run: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class DecisionResponse(BaseModel):
    """The ``decide --json`` payload plus ``api_version``.

    ``ok: false`` with ``diagnostics`` is a domain failure — an invalid
    decision value, an unknown proposal, or a missing reviewer. The
    ``{error}`` shape stays transport-only; lock contention arrives as
    503 ``STORAGE.WRITE_BUSY`` so a retry is meaningful (ADR 0003).
    """

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    decision_id: str | None = None
    proposal_id: str | None = None
    decision: str | None = None
    recorded: bool | None = None
    diagnostics: list[dict[str, Any]] | None = None


class IngestResponse(BaseModel):
    """The ``ingest --json`` payload plus ``api_version``. ``snapshot``
    is the staged label the console submitted, not a server path."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    valid: bool | None = None
    snapshot: str | None = None
    ingest_id: str | None = None
    source: str | None = None
    content_digest: str | None = None
    mapping_digest: str | None = None
    observed_at: str | None = None
    accepted: int | None = None
    rejected: int | None = None
    canonical: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class ProjectRunResponse(BaseModel):
    """The ``project --json`` payload plus ``api_version``: a frozen
    projection run with its candidates on success."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    valid: bool | None = None
    run_id: str | None = None
    projection: str | None = None
    as_of: str | None = None
    definition_digest: str | None = None
    recorded: bool | None = None
    candidates: list[dict[str, Any]] | None = None
    inputs: list[dict[str, Any]] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class DiagnoseRunResponse(BaseModel):
    """The ``diagnose --json`` payload plus ``api_version``: the
    diagnosis run with validated proposals and rejections."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    valid: bool | None = None
    run_id: str | None = None
    projection_run_id: str | None = None
    provider: str | None = None
    model: str | None = None
    status: str | None = None
    candidate_count: int | None = None
    deliverables: dict[str, Any] | None = None
    proposals: list[dict[str, Any]] | None = None
    rejected: list[dict[str, Any]] | None = None
    recorded: bool | None = None
    diagnostics: list[dict[str, Any]] | None = None


class EvaluateRunTriggerResponse(BaseModel):
    """The ``evaluate --json`` payload plus ``api_version``: the frozen
    report on success (distinct from the read-only
    ``EvaluationRunResponse``)."""

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    valid: bool | None = None
    run_id: str | None = None
    evaluation: str | None = None
    as_of: str | None = None
    boundary: str | None = None
    contract_digest: str | None = None
    recorded: bool | None = None
    report: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class ExecuteResponse(BaseModel):
    """The ``execute --json`` payload plus ``api_version`` (ADR 0004).

    ``ok: false`` with ``diagnostics`` covers the refusal paths —
    ``DECISION.NOT_APPROVED``, ``EXECUTION.UNKNOWN_PROPOSAL``, store
    errors. A replayed execution reports ``replayed: true`` with the
    recorded receipt and appends nothing.
    """

    model_config = ConfigDict(extra="allow")

    api_version: str
    workspace: str
    ok: bool
    execution_id: str | None = None
    idempotency_key: str | None = None
    proposal_id: str | None = None
    adapter: str | None = None
    status: str | None = None
    attempt: int | None = None
    attempts: int | None = None
    replayed: bool | None = None
    receipt: dict[str, Any] | None = None
    error: str | None = None
    recorded: bool | None = None
    diagnostics: list[dict[str, Any]] | None = None


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
