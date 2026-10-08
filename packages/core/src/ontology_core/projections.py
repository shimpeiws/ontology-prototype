"""Deterministic projections over canonical state, frozen per run.

Projection semantics are declared on the domain projection itself — the
``select`` block names the required/absent events, an optional
as-of-relative elapsed comparison, and an optional condition over the
subject entity's latest observed properties. There is no code registry:
a new workflow authors its selection in ``domain.yaml``.

A run requires an explicit aware ``as_of`` and persists the projection
definition digest, the per-subject input references it examined, and the
full candidate set into ``projection_runs`` — so a historical run is
inspectable and unchanged by later ingestion or definition edits. A
generic projection language is a non-goal; the selection vocabulary is
deliberately restricted.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import (
    CanonicalStore,
    SqliteCanonicalStore,
    store_error_code,
)
from .conditions import evaluate_condition
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR
from .model import ElapsedSpec, ProjectionSpec, Workspace
from .trace import JsonlTraceSink, TraceSink
from .workspace import SUPPORTED_SCHEMA_VERSION

# Version of the declarative selection evaluator. Bump when the selection
# rules change so previously frozen runs carry a different definition
# digest.
_SEMANTIC_VERSION = 1

_UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}


@dataclass(frozen=True)
class ProjectionCandidate:
    """One subject selected by a projection run."""

    subject_entity: str
    subject_id: str
    occurred_at: datetime
    elapsed_seconds: float
    # The subject entity's latest *observed* property set, which may postdate
    # as_of — see ``evidence["entity_updated_at"]`` for its stamp.
    properties: dict[str, Any]
    evidence: dict[str, Any]
    # Declared type of each property ("ref:<Entity>", "string", ...) so
    # consumers can match refs without re-reading the domain definition.
    property_types: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": {"entity": self.subject_entity, "id": self.subject_id},
            "occurred_at": _iso_z(self.occurred_at),
            "elapsed_seconds": self.elapsed_seconds,
            "elapsed_days": round(self.elapsed_seconds / 86400, 6),
            "properties": self.properties,
            "property_types": self.property_types,
            "evidence": self.evidence,
        }


@dataclass
class ProjectionResult:
    """Outcome of ``run_projection``: diagnostics, the run identity, and the
    candidates selected at ``as_of``."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    projection: str | None = None
    as_of: datetime | None = None
    run_id: str | None = None
    definition_digest: str | None = None
    candidates: list[ProjectionCandidate] = field(default_factory=list)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    recorded: bool = False

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


def run_projection(
    workspace: Workspace,
    projection: str,
    *,
    as_of: datetime,
    store: CanonicalStore | None = None,
    trace: TraceSink | None = None,
) -> ProjectionResult:
    """Compute one projection at an explicit ``as_of`` and freeze the run.

    Never raises on bad input: an unknown or unselectable projection, a
    naive ``as_of``, and store failures all become diagnostics.
    """
    diag = Diagnostics()
    result = ProjectionResult(projection=projection)

    spec = workspace.domain.projections.get(projection)
    if spec is None:
        diag.error(
            "PROJECTION.UNKNOWN_PROJECTION",
            f"unknown projection {projection!r}",
            hint=(
                "declared projections: "
                + ", ".join(sorted(workspace.domain.projections))
            ),
        )
    elif spec.select is None or not spec.select.required_events:
        # Unreachable through the loader — 'select' is required — but a
        # programmatically built ProjectionSpec can still lack one.
        diag.error(
            "PROJECTION.MISSING_SELECTION",
            f"projection {projection!r} declares no selection",
        )

    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        diag.error(
            "PROJECTION.NAIVE_AS_OF",
            "as_of must be an explicit timezone-aware time",
            hint="use an explicit offset (e.g. 'Z' or '+09:00')",
        )
    else:
        result.as_of = as_of.astimezone(timezone.utc)

    if diag.has_errors or spec is None or result.as_of is None:
        result.diagnostics = diag.items
        return result

    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    if trace is None:
        trace = JsonlTraceSink(Path(workspace.path) / RUNTIME_DIR / "trace.jsonl")

    created_at = datetime.now(timezone.utc)
    result.run_id = (
        f"prj-{created_at:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}"
    )
    result.definition_digest = _definition_digest(workspace, projection, spec)
    try:
        result.candidates, result.inputs = _select_candidates(
            workspace, store, spec, result.as_of, diag
        )
    except Exception as exc:
        # Canonical reads go through the pluggable store boundary: a locked or
        # corrupt database (or a custom store's own exception) becomes a
        # diagnostic, never a traceback through the CLI.
        diag.error(
            store_error_code(exc, "PROJECTION.STORE_ERROR"),
            f"cannot read canonical state: {exc}",
        )

    # A run that produced errors is not frozen into projection_runs: a
    # recorded run must always mean "evaluated successfully", so a failed
    # evaluation can never masquerade as an empty candidate list.
    if not diag.has_errors:
        try:
            store.record_projection_run(
                run_id=result.run_id,
                projection=projection,
                subject=spec.subject,
                as_of=result.as_of,
                created_at=created_at,
                definition_digest=result.definition_digest,
                inputs=result.inputs,
                candidates=[c.to_dict() for c in result.candidates],
            )
            result.recorded = True
        except Exception as exc:
            diag.error(
                store_error_code(exc, "PROJECTION.STORE_ERROR"),
                f"cannot record projection run: {exc}",
            )

    try:
        trace.emit(
            "projection",
            "projection.failed" if diag.has_errors else "projection.completed",
            {
                "run_id": result.run_id,
                "projection": projection,
                "as_of": _iso_z(result.as_of),
                "definition_digest": result.definition_digest,
                "candidates": len(result.candidates),
                "inputs": len(result.inputs),
                "recorded": result.recorded,
                "errors": [
                    d.code for d in diag.items if d.severity == "error"
                ],
            },
        )
    except OSError as exc:
        diag.warning(
            "PROJECTION.TRACE_ERROR",
            f"cannot write trace: {exc.strerror or exc}",
        )
    result.diagnostics = diag.items
    return result


def get_projection_run(
    workspace: Workspace, run_id: str, *, store: CanonicalStore | None = None
) -> dict[str, Any] | None:
    """Read back a frozen projection run by id."""
    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    return store.projection_run(run_id)


def _select_candidates(
    workspace: Workspace,
    store: CanonicalStore,
    spec: ProjectionSpec,
    as_of: datetime,
    diag: Diagnostics,
) -> tuple[list[ProjectionCandidate], list[dict[str, Any]]]:
    """Evaluate the projection's declared ``select`` over canonical events.

    A subject is a candidate when every required event had occurred, no
    absent event had occurred by ``as_of``, the optional ``elapsed``
    comparison on the anchor event's earliest occurrence holds, and the
    optional ``where`` condition matches the subject's latest observed
    entity properties. The anchor is ``elapsed.event`` — the first
    required event when ``elapsed`` is undeclared.
    """
    sel = spec.select
    anchor = sel.elapsed.event if sel.elapsed is not None else sel.required_events[0]
    absent_set = set(sel.absent_events)

    # Defense for programmatically built specs: every referenced event
    # must exist and be bound to the projection subject. The loader
    # reports this earlier as DOMAIN.EVENT_SUBJECT_MISMATCH.
    referenced = dict.fromkeys((*sel.required_events, *sel.absent_events))
    offending = [
        name
        for name in referenced
        if name not in workspace.domain.events
        or workspace.domain.events[name].subject != spec.subject
    ]
    if offending:
        diag.error(
            "PROJECTION.SUBJECT_MISMATCH",
            f"projection subject {spec.subject!r} does not match the "
            f"selection's event subjects ({', '.join(offending)})",
        )
        return [], []

    candidates: list[ProjectionCandidate] = []
    inputs: list[dict[str, Any]] = []
    for subject_id in store.subject_ids_with_event(anchor, spec.subject):
        facts = store.events_for_subject(spec.subject, subject_id)
        starts = [f for f in facts if f.event == anchor]
        # Only absent events that had occurred by as_of exclude — one
        # occurring later does not change what was true at as_of.
        ends = [
            f
            for f in facts
            if f.event in absent_set and f.occurred_at <= as_of
        ]
        future_ends = [
            f
            for f in facts
            if f.event in absent_set and f.occurred_at > as_of
        ]
        first = min(starts, key=lambda f: f.occurred_at)
        entry: dict[str, Any] = {
            "subject_id": subject_id,
            "start_fact_keys": [f.fact_key for f in starts],
            "end_fact_keys": [f.fact_key for f in ends],
        }
        if future_ends:
            entry["future_end_fact_keys"] = [f.fact_key for f in future_ends]
        inputs.append(entry)

        if ends:
            entry["excluded"] = "interval end event exists"
            continue

        missing = False
        for name in sel.required_events:
            if name == anchor:
                continue
            occurrences = [f for f in facts if f.event == name]
            by_as_of = [f for f in occurrences if f.occurred_at <= as_of]
            keys = entry.setdefault("required_fact_keys", {})
            keys[name] = [f.fact_key for f in occurrences]
            if by_as_of:
                continue
            entry["excluded"] = f"required event {name!r} had not occurred"
            if occurrences:
                diag.warning(
                    "PROJECTION.FUTURE_EVENT",
                    f"{spec.subject}/{subject_id}: {name} "
                    f"occurred at {_iso_z(occurrences[0].occurred_at)}, "
                    f"after as_of {_iso_z(as_of)}",
                )
            missing = True
            break
        if missing:
            continue

        elapsed = (as_of - first.occurred_at).total_seconds()
        if elapsed < 0:
            entry["excluded"] = "future-dated start event"
            diag.warning(
                "PROJECTION.FUTURE_EVENT",
                f"{spec.subject}/{subject_id}: {anchor} "
                f"occurred at {_iso_z(first.occurred_at)}, after as_of "
                f"{_iso_z(as_of)}",
            )
            continue
        if sel.elapsed is not None and not _meets_elapsed(elapsed, sel.elapsed):
            entry["excluded"] = (
                "elapsed within threshold"
                if sel.elapsed.comparator in ("gt", "gte")
                else "elapsed outside threshold"
            )
            continue

        links = store.event_links(first.fact_key)
        entity = store.entity_state(spec.subject, subject_id)
        properties = entity["properties"] if entity else {}
        if sel.where is not None and not evaluate_condition(
            sel.where, properties
        ):
            entry["excluded"] = "subject condition not met"
            continue
        candidates.append(
            ProjectionCandidate(
                subject_entity=spec.subject,
                subject_id=subject_id,
                occurred_at=first.occurred_at,
                elapsed_seconds=elapsed,
                properties=properties,
                property_types={
                    name: (
                        f"{spec.type}:{spec.ref}" if spec.ref else spec.type
                    )
                    for name, spec in workspace.domain.entities[
                        spec.subject
                    ].properties.items()
                },
                evidence={
                    "fact_key": first.fact_key,
                    "observation_ids": [obs for obs, _ in links],
                    "ingest_ids": sorted({ing for _, ing in links}),
                    "entity_observation_id": (
                        entity["last_observation_id"] if entity else None
                    ),
                    # Entity properties are the latest *observed* state and
                    # may postdate as_of — expose the stamp so consumers can
                    # tell how current the candidate's properties are.
                    "entity_updated_at": entity["updated_at"] if entity else None,
                },
            )
        )
    return candidates, inputs


def _meets_elapsed(elapsed_seconds: float, elapsed: ElapsedSpec) -> bool:
    """The declared as-of-relative duration comparison, in seconds."""
    threshold = elapsed.value * _UNIT_SECONDS[elapsed.unit]
    if elapsed.comparator == "lt":
        return elapsed_seconds < threshold
    if elapsed.comparator == "lte":
        return elapsed_seconds <= threshold
    if elapsed.comparator == "gt":
        return elapsed_seconds > threshold
    return elapsed_seconds >= threshold


def _definition_digest(
    workspace: Workspace, projection: str, spec: ProjectionSpec
) -> str:
    """Digest of everything that defines what this run computes.

    Covers the schema version, the projection declaration (including the
    full declarative selection), the evaluator version, the full
    evaluation contract, the referenced events' domain declarations, and
    the date-only timezone policy — so a definition or contract edit
    changes the digest even when the candidate list happens to stay the
    same.
    """
    interval = workspace.evaluation.interval
    sel = spec.select
    referenced = dict.fromkeys(
        (
            *(sel.required_events if sel is not None else ()),
            *(sel.absent_events if sel is not None else ()),
            interval.start_event,
            interval.end_event,
        )
    )
    raw = json.dumps(
        {
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "projection": dataclasses.asdict(spec),
            "semantics": {
                "id": "declared-selection",
                "version": _SEMANTIC_VERSION,
            },
            "evaluation": dataclasses.asdict(workspace.evaluation),
            "events": {
                name: dataclasses.asdict(workspace.domain.events[name])
                for name in referenced
                if name in workspace.domain.events
            },
            "date_only_timezone": (
                workspace.domain.policies.event_times.date_only_timezone
            ),
        },
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ProjectionCandidate",
    "ProjectionResult",
    "get_projection_run",
    "run_projection",
]
