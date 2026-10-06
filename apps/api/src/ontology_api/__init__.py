"""ontology-api: local HTTP API over the shared ontology-core.

The API owns transport concerns only — request/response typing, root
containment, and error mapping. All workspace validation is delegated to
``ontology_core``; the core never imports this package.
"""

__version__ = "0.0.1"

from .config import ROOTS_ENV, ApiConfig
from .main import create_app
from .service import (
    ApiError,
    CoreWorkspaceService,
    DraftPreviewResult,
    DraftResult,
    OpenResult,
    SaveOutcome,
    WorkspaceService,
    contract_metadata,
)

__all__ = [
    "ROOTS_ENV",
    "ApiConfig",
    "ApiError",
    "CoreWorkspaceService",
    "DraftPreviewResult",
    "DraftResult",
    "OpenResult",
    "SaveOutcome",
    "WorkspaceService",
    "__version__",
    "contract_metadata",
    "create_app",
]
