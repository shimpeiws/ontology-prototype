"""Typed workspace definition objects.

These dataclasses are the transport-independent contract between the core and
any app. Validation builds them only when the corresponding file is error free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SCALAR_TYPES = ("string", "integer", "number", "boolean", "date", "datetime")
PROPERTY_TYPES = SCALAR_TYPES + ("ref",)
CAST_TYPES = SCALAR_TYPES

# Ordering comparators shared by evaluation targets/guardrails and the
# projection elapsed threshold ('lt' is strict). Equality on a measured
# duration is not offered.
ORDER_COMPARATORS = ("lt", "lte", "gt", "gte")

# Duration units usable wherever a duration is expressed: the evaluation
# metric unit and the projection elapsed threshold.
DURATION_UNITS = ("seconds", "minutes", "hours", "days")


@dataclass(frozen=True)
class PropertySpec:
    name: str
    type: str
    required: bool = False
    ref: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class EntityType:
    name: str
    properties: dict[str, PropertySpec] = field(default_factory=dict)
    description: str | None = None


@dataclass(frozen=True)
class EventType:
    name: str
    subject: str
    properties: dict[str, PropertySpec] = field(default_factory=dict)
    description: str | None = None


@dataclass(frozen=True)
class ElapsedSpec:
    """As-of-relative duration comparison over a required event.

    ``event`` is the anchor event (always one of the selection's required
    events); the comparison is ``as_of - earliest(event occurred_at)``
    ``comparator`` ``value`` ``unit``.
    """

    event: str
    comparator: str
    value: float
    unit: str


@dataclass(frozen=True)
class Condition:
    """A validated declarative condition tree (see conditions.py)."""

    raw: dict[str, Any]


@dataclass(frozen=True)
class SelectionSpec:
    """Declarative candidate selection declared on a domain projection.

    A subject is a candidate when every ``required_events`` type has at
    least one recorded occurrence, no ``absent_events`` type had occurred
    by the run's as_of, the optional ``elapsed`` comparison holds, and the
    optional ``where`` condition matches the subject entity's latest
    observed properties.
    """

    required_events: tuple[str, ...]
    absent_events: tuple[str, ...] = ()
    elapsed: ElapsedSpec | None = None
    where: Condition | None = None


@dataclass(frozen=True)
class ProjectionSpec:
    name: str
    subject: str
    select: SelectionSpec
    description: str | None = None


@dataclass(frozen=True)
class EventTimePolicy:
    """Explicit temporal policy for canonical events.

    Every event carries an occurrence time (``occurrence_field``) and an
    observation time (``observation_field``). Date-only values are interpreted
    as midnight in ``date_only_timezone`` (only ``UTC`` is supported in
    schema version 1).
    """

    occurrence_field: str
    observation_field: str
    date_only_timezone: str


@dataclass(frozen=True)
class DomainPolicies:
    """Explicit workspace policies. ``entity_id_type`` is always ``string``:
    entity identifiers are opaque strings and are never coerced to numbers."""

    entity_id_type: str
    event_times: EventTimePolicy


@dataclass(frozen=True)
class DomainDefinition:
    name: str
    policies: DomainPolicies
    entities: dict[str, EntityType] = field(default_factory=dict)
    events: dict[str, EventType] = field(default_factory=dict)
    projections: dict[str, ProjectionSpec] = field(default_factory=dict)
    description: str | None = None


@dataclass(frozen=True)
class FieldAssignment:
    """Maps one value from a source record: exactly one of ``field`` /
    ``literal``, plus an optional explicit scalar ``cast``."""

    field: str | None = None
    literal: str | int | float | bool | None = None
    cast: str | None = None


@dataclass(frozen=True)
class EntityBinding:
    entity: str
    id: FieldAssignment
    properties: dict[str, FieldAssignment] = field(default_factory=dict)


@dataclass(frozen=True)
class SubjectRef:
    entity: str
    id: FieldAssignment


@dataclass(frozen=True)
class EventBinding:
    event: str
    subject: SubjectRef
    occurred_at: FieldAssignment
    when: Condition | None = None
    observed_at: FieldAssignment | None = None


@dataclass(frozen=True)
class SourceMapping:
    """One declared source: its snapshot format and the bindings its records
    resolve through.

    A workspace mapping declares one or more of these; ingest and preview
    select the source per snapshot, so a record is only ever resolved
    through its own source's ``entities``/``events``.
    """

    name: str
    format: str
    entities: list[EntityBinding] = field(default_factory=list)
    events: list[EventBinding] = field(default_factory=list)


@dataclass(frozen=True)
class MappingDefinition:
    name: str
    sources: list[SourceMapping]
    description: str | None = None


@dataclass(frozen=True)
class IntervalSpec:
    start_event: str
    end_event: str


@dataclass(frozen=True)
class TargetSpec:
    comparator: str
    value: float


@dataclass(frozen=True)
class GuardrailSpec:
    name: str
    metric: str
    comparator: str
    value: float | None = None
    description: str | None = None


@dataclass(frozen=True)
class MetricSpec:
    """A declared guardrail metric: a numerator/denominator rate over
    entities related to the interval's cohort subjects through a ref.

    ``entity`` is the counted entity type and ``ref`` its ref-typed
    property linking each entity to a cohort subject — the ref's declared
    target types the metric to intervals on that subject entity. Exactly
    one predicate form is declared:

    - ``flag`` — a boolean property. Entities carrying a boolean value
      form the denominator and ``true`` values the numerator; an entity
      without a boolean value is an evidence gap.
    - ``where`` — a condition over the entity's properties. Every linked
      entity is in the denominator and matching entities the numerator.

    ``noun`` is the report noun for the numerator count (default
    ``"<flag>s"`` for a flag predicate, ``"matched"`` for a ``where``
    predicate)."""

    name: str
    entity: str
    ref: str
    flag: str | None = None
    where: Condition | None = None
    noun: str | None = None
    description: str | None = None

    def evidence_nouns(self) -> tuple[str, str]:
        """The report nouns for the metric's denominator and numerator.

        The denominator names the counted entities — the entity name
        lowercased with an ``s`` appended. The numerator names the
        predicate-true count: the declared ``noun``, else ``"<flag>s"``
        for a flag predicate or ``"matched"`` for a ``where`` predicate.
        """
        denominator = f"{self.entity.lower()}s"
        numerator = self.noun or (
            f"{self.flag}s" if self.flag is not None else "matched"
        )
        return denominator, numerator

    def evidence_keys(self) -> list[str]:
        """The report keys a cohort evidence bucket emits, in order."""
        denominator, numerator = self.evidence_nouns()
        keys = [denominator, numerator, "rate"]
        if self.flag is not None:
            keys.append(f"{denominator}_missing_{self.flag}")
        keys += ["postdating_as_of", "entity_ids"]
        return keys


@dataclass(frozen=True)
class EvaluationDefinition:
    name: str
    interval: IntervalSpec
    metric: str
    aggregation: str
    unit: str
    baseline: float
    target: TargetSpec
    guardrails: list[GuardrailSpec] = field(default_factory=list)
    metrics: dict[str, MetricSpec] = field(default_factory=dict)
    description: str | None = None


@dataclass(frozen=True)
class TaskSpec:
    goal: str
    deliverables: list[str]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    params: dict[str, PropertySpec] = field(default_factory=dict)
    description: str | None = None


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    projection: str
    task: TaskSpec
    permitted_actions: dict[str, ActionSpec] = field(default_factory=dict)
    description: str | None = None


@dataclass(frozen=True)
class Workspace:
    """A fully validated workspace: the four definitions an FDE checks in."""

    path: Path
    domain: DomainDefinition
    mapping: MappingDefinition
    evaluation: EvaluationDefinition
    agent: AgentDefinition


def param_type_error(value: Any, spec: PropertySpec) -> str | None:
    """A rejection reason when ``value`` does not satisfy ``spec``."""
    if spec.type == "ref":
        if isinstance(value, str) and value:
            return None
        return f"must be {spec.type} (a {spec.ref} id string)"
    if spec.type == "string":
        return None if isinstance(value, str) else "must be string"
    if spec.type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return None
        return "must be integer"
    if spec.type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return None
        return "must be number"
    if spec.type == "boolean":
        return None if isinstance(value, bool) else "must be boolean"
    if spec.type == "date":
        # Strict date-only: datetimes must not validate as dates.
        if isinstance(value, str) and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", value
        ):
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                pass
            else:
                return None
        return "must be date (YYYY-MM-DD)"
    if spec.type == "datetime":
        # Datetimes carry an explicit offset, like the projection as_of.
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                pass
            else:
                if parsed.tzinfo is not None:
                    return None
        return "must be datetime (ISO 8601 with an offset)"
    return f"unsupported param type {spec.type!r}"
