"""ontology-core: shared operational core for the FDE operational loop.

This package owns workspace contracts, loading, and validation. It is
transport independent: it never imports app code (CLI, API, Web) and needs
only PyYAML at runtime.
"""

from .canonical import (
    ApplyResult,
    CanonicalEvent,
    CanonicalStore,
    SqliteCanonicalStore,
    event_fact_key,
)
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR, EvidenceStore, FileEvidenceStore
from .ingest import (
    IngestResult,
    Observation,
    ResolvedEntity,
    ResolvedEvent,
    ingest_snapshot,
)
from .model import (
    ActionSpec,
    AgentDefinition,
    Condition,
    DomainDefinition,
    DomainPolicies,
    EntityBinding,
    EntityType,
    EvaluationDefinition,
    EventBinding,
    EventTimePolicy,
    EventType,
    FieldAssignment,
    GuardrailSpec,
    IntervalSpec,
    MappingDefinition,
    ProjectionSpec,
    PropertySpec,
    SourceSpec,
    SubjectRef,
    TargetSpec,
    TaskSpec,
    Workspace,
)
from .trace import JsonlTraceSink, NullTraceSink, TraceSink
from .workspace import (
    SUPPORTED_SCHEMA_VERSION,
    WORKSPACE_FILES,
    LoadResult,
    load_workspace,
)

__version__ = "0.0.1"

__all__ = [
    "ActionSpec",
    "AgentDefinition",
    "ApplyResult",
    "CanonicalEvent",
    "CanonicalStore",
    "Condition",
    "Diagnostic",
    "Diagnostics",
    "DomainDefinition",
    "DomainPolicies",
    "EntityBinding",
    "EntityType",
    "EvaluationDefinition",
    "EventBinding",
    "EventTimePolicy",
    "EventType",
    "EvidenceStore",
    "FieldAssignment",
    "FileEvidenceStore",
    "GuardrailSpec",
    "IngestResult",
    "IntervalSpec",
    "JsonlTraceSink",
    "LoadResult",
    "MappingDefinition",
    "NullTraceSink",
    "Observation",
    "ProjectionSpec",
    "PropertySpec",
    "RUNTIME_DIR",
    "ResolvedEntity",
    "ResolvedEvent",
    "SUPPORTED_SCHEMA_VERSION",
    "SourceSpec",
    "SqliteCanonicalStore",
    "SubjectRef",
    "TargetSpec",
    "TaskSpec",
    "TraceSink",
    "WORKSPACE_FILES",
    "Workspace",
    "__version__",
    "event_fact_key",
    "ingest_snapshot",
    "load_workspace",
]
