"""FastAPI application factory for the local ontology API.

``create_app`` wires transport (routes, typed models, error mapping) to an
injectable ``WorkspaceService``. With no arguments it reads
``ONTOLOGY_API_ROOTS``/``ONTOLOGY_API_HOST``/``ONTOLOGY_API_PORT``; tests
inject a config or a stub service directly.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .config import ApiConfig
from .schemas import (
    ContractResponse,
    DiagnosticOut,
    DraftValidateRequest,
    DraftValidateResponse,
    ErrorResponse,
    OpenRequest,
    OpenResponse,
    WorkspaceRef,
)
from .service import (
    ApiError,
    CoreWorkspaceService,
    WorkspaceService,
    contract_metadata,
)

API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"


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

    return app


app = create_app()
