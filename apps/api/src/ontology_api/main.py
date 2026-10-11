"""FastAPI application factory for the local ontology API.

``create_app`` wires transport (routes, typed models, error mapping) to an
injectable ``WorkspaceService``. With no arguments it reads
``ONTOLOGY_API_ROOTS``/``ONTOLOGY_API_HOST``/``ONTOLOGY_API_PORT``; tests
inject a config or a stub service directly.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import ApiConfig
from .schemas import (
    API_PREFIX,
    API_VERSION,
    ContractResponse,
    DecideRequest,
    DecisionResponse,
    DiagnosticOut,
    DiagnoseRunRequest,
    DiagnoseRunResponse,
    DraftPreviewRequest,
    DraftPreviewResponse,
    DraftValidateRequest,
    DraftValidateResponse,
    ErrorResponse,
    EvaluateRunTriggerRequest,
    EvaluateRunTriggerResponse,
    EvaluationRunRequest,
    EvaluationRunResponse,
    EvaluationRunsResponse,
    ExecuteRequest,
    ExecuteResponse,
    IngestRequest,
    IngestResponse,
    OpenRequest,
    OpenResponse,
    ProjectRunRequest,
    ProjectRunResponse,
    ProposalRequest,
    ProposalResponse,
    ProposalsResponse,
    SaveRequest,
    SaveResponse,
    TraceOverviewResponse,
    TraceRunRequest,
    TraceSubjectRequest,
    TraceViewResponse,
    WorkspacePathRequest,
    WorkspaceRef,
)
from .service import (
    ApiError,
    CoreWorkspaceService,
    WorkspaceService,
    contract_metadata,
)

# Browser access is origin-allowlisted (``ApiConfig.allowed_origins``):
# the Vite dev proxy keeps the console same-origin, so cross-origin
# approval is needed only for direct calls from the console's own
# dev/preview origins. A page on any other origin — loopback ports
# included — fails preflight and cannot drive the write-side triggers.

logger = logging.getLogger("ontology_api")


def create_app(
    config: ApiConfig | None = None, service: WorkspaceService | None = None
) -> FastAPI:
    config = config if config is not None else ApiConfig.from_env()
    service = (
        service
        if service is not None
        else CoreWorkspaceService(config.workspace_roots)
    )
    app = FastAPI(
        title="ontology-api",
        version=__version__,
        description=(
            "Local API over ontology-core workspace contracts: open/inspect "
            "a workspace under configured roots, validate draft bundles, and "
            "read the versioned contract the Web app codes against."
        ),
    )
    app.state.service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    def get_service(request: Request) -> WorkspaceService:
        return request.app.state.service

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        detail = [
            {"loc": list(e.get("loc", ())), "type": e.get("type"), "msg": e.get("msg")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "API.MALFORMED_REQUEST",
                    "message": "request failed contract validation",
                    "detail": detail,
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {
            404: "API.NOT_FOUND",
            405: "API.METHOD_NOT_ALLOWED",
        }.get(exc.status_code, "API.HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": code, "message": str(exc.detail or exc)}
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error in %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "API.INTERNAL_ERROR",
                    "message": "internal server error",
                }
            },
        )

    @app.get(f"{API_PREFIX}/contract", response_model=ContractResponse)
    def get_contract() -> dict:
        return contract_metadata()

    @app.post(
        f"{API_PREFIX}/workspaces/open",
        response_model=OpenResponse,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def open_workspace(
        payload: OpenRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> OpenResponse:
        result = service.open(payload.path)
        return OpenResponse(
            api_version=API_VERSION,
            valid=result.valid,
            workspace=WorkspaceRef(
                path=str(result.path), digest=result.digest
            ),
            files=result.files,
            definitions=result.definitions,
            diagnostics=[DiagnosticOut(**d.to_dict()) for d in result.diagnostics],
        )

    @app.post(
        f"{API_PREFIX}/workspaces/drafts/validate",
        response_model=DraftValidateResponse,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def validate_draft(
        payload: DraftValidateRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> DraftValidateResponse:
        result = service.validate_draft(payload.path, payload.files)
        return DraftValidateResponse(
            api_version=API_VERSION,
            valid=result.valid,
            digest=result.digest,
            definitions=result.definitions,
            diagnostics=[DiagnosticOut(**d.to_dict()) for d in result.diagnostics],
        )

    @app.post(
        f"{API_PREFIX}/workspaces/save",
        response_model=SaveResponse,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def save_workspace_endpoint(
        payload: SaveRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> SaveResponse:
        result = service.save(payload.path, payload.files, payload.base_digest)
        return SaveResponse(
            api_version=API_VERSION,
            outcome=result.outcome,
            valid=result.valid,
            digest=result.digest,
            definitions=result.definitions,
            diagnostics=[DiagnosticOut(**d.to_dict()) for d in result.diagnostics],
            message=result.message,
        )

    @app.post(
        f"{API_PREFIX}/workspaces/drafts/preview",
        response_model=DraftPreviewResponse,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def preview_draft(
        payload: DraftPreviewRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> DraftPreviewResponse:
        result = service.preview_draft(
            payload.path,
            payload.files,
            payload.sample.text,
            payload.sample.source,
        )
        return DraftPreviewResponse(
            api_version=API_VERSION,
            valid=result.valid,
            digest=result.digest,
            diagnostics=[DiagnosticOut(**d.to_dict()) for d in result.diagnostics],
            preview=result.preview,
        )

    # ------------------------------------------------------- inspection
    #
    # Operational surface (ADR 0003): read-only views over `.ontology/`.
    # The service returns the CLI's `--json` payload verbatim; each route
    # only adds `api_version`. Domain misses arrive as `ok: false` with
    # core diagnostics — `{error}` stays reserved for transport failures.

    @app.post(
        f"{API_PREFIX}/workspaces/trace",
        response_model=TraceOverviewResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def trace_overview(
        payload: WorkspacePathRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.trace_overview(payload.path),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/trace/subject",
        response_model=TraceViewResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def trace_subject(
        payload: TraceSubjectRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.trace_subject(payload.path, payload.subject),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/trace/run",
        response_model=TraceViewResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def trace_run(
        payload: TraceRunRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.trace_run(payload.path, payload.run_id),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/evaluations",
        response_model=EvaluationRunsResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def evaluation_runs(
        payload: WorkspacePathRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.evaluation_runs(payload.path),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/evaluations/run",
        response_model=EvaluationRunResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def evaluation_run(
        payload: EvaluationRunRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.evaluation_run(payload.path, payload.run_id),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/proposals",
        response_model=ProposalsResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def proposals(
        payload: WorkspacePathRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.proposals(payload.path),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/proposals/show",
        response_model=ProposalResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def proposal(
        payload: ProposalRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.proposal(payload.path, payload.proposal_id),
        }

    # ------------------------------------------------- decision write
    #
    # The human approve/reject write (ADR 0003): a decision recorded
    # through the core's `decide`. Domain outcomes stay
    # `ok`/`diagnostics` payloads; lock contention is transport-level —
    # 503 STORAGE.WRITE_BUSY.

    @app.post(
        f"{API_PREFIX}/workspaces/proposals/decide",
        response_model=DecisionResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def decide_proposal(
        payload: DecideRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.decide(
                payload.path,
                payload.proposal_id,
                payload.decision,
                reviewer=payload.reviewer,
                reason=payload.reason,
            ),
        }

    # --------------------------------------------------- run triggers
    #
    # ADR 0004: one trigger per loop stage, each returning the CLI's
    # `--json` payload verbatim plus `api_version`. The same failure
    # split applies as everywhere on this surface — domain outcomes are
    # `ok`/`diagnostics` payloads, lock contention is 503
    # STORAGE.WRITE_BUSY.

    @app.post(
        f"{API_PREFIX}/workspaces/ingest",
        response_model=IngestResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def ingest_snapshot_endpoint(
        payload: IngestRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.ingest(
                payload.path,
                payload.snapshot.text,
                source=payload.snapshot.source,
                name=payload.snapshot.name,
            ),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/project",
        response_model=ProjectRunResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def project_run(
        payload: ProjectRunRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.project(
                payload.path,
                payload.as_of,
                projection=payload.projection,
            ),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/diagnose",
        response_model=DiagnoseRunResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def diagnose_run(
        payload: DiagnoseRunRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.diagnose(
                payload.path,
                run_id=payload.run_id,
                as_of=payload.as_of,
                provider=payload.provider,
                model=payload.model,
            ),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/evaluate",
        response_model=EvaluateRunTriggerResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def evaluate_run(
        payload: EvaluateRunTriggerRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.evaluate(
                payload.path,
                boundary=payload.boundary,
                as_of=payload.as_of,
            ),
        }

    @app.post(
        f"{API_PREFIX}/workspaces/execute",
        response_model=ExecuteResponse,
        response_model_exclude_unset=True,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def execute_proposal(
        payload: ExecuteRequest,
        service: WorkspaceService = Depends(get_service),
    ) -> dict:
        return {
            "api_version": API_VERSION,
            **service.execute(payload.path, payload.proposal_id),
        }

    return app


app = create_app()
