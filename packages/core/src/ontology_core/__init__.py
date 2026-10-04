"""ontology-core: shared operational core for the FDE operational loop.

This package owns workspace contracts, loading, and validation. It is
transport independent: it never imports app code (CLI, API, Web) and needs
only PyYAML at runtime.
"""

from .diagnostics import Diagnostic, Diagnostics
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
    "FieldAssignment",
    "GuardrailSpec",
    "IntervalSpec",
    "LoadResult",
    "MappingDefinition",
    "ProjectionSpec",
    "PropertySpec",
    "SUPPORTED_SCHEMA_VERSION",
    "SourceSpec",
    "SubjectRef",
    "TargetSpec",
    "TaskSpec",
    "WORKSPACE_FILES",
    "Workspace",
    "__version__",
    "load_workspace",
]
