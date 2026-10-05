"""Deterministic projections over canonical state, frozen per run.

v0.1 implements one projection semantic, registered under the domain
projection name ``CompletedButNotInvoiced``: subjects where the evaluation
contract's ``interval.start_event`` occurred but no ``interval.end_event``
exists, and the elapsed time from the start event's ``occurred_at`` to an
explicit ``as_of`` is strictly greater than two days.

A run requires an explicit aware ``as_of`` and persists the projection
definition digest, the per-subject input references it examined, and the
full candidate set into ``projection_runs`` — so a historical run is
inspectable and unchanged by later ingestion or definition edits. A generic
projection language is a v0.1 non-goal; new projection kinds need their own
registered semantics.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .canonical import CanonicalStore, SqliteCanonicalStore
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR
from .model import Workspace
from .trace import JsonlTraceSink, TraceSink
from .workspace import SUPPORTED_SCHEMA_VERSION

# Fixed v0.1 threshold for CompletedButNotInvoiced (see issue #4): strictly
# greater than two days — exactly two days is not a candidate.
DELAY_THRESHOLD = timedelta(days=2)


@dataclass(frozen=True)
class ProjectionCandidate:
    """One subject selected by a projection run."""

    subject_entity: str
    subject_id: str
    occurred_at: datetime
    elapsed_seconds: float
    properties: dict[str, Any]
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": {"entity": self.subject_entity, "id": self.subject_id},
            "occurred_at": _iso_z(self.occurred_at),
            "elapsed_seconds": self.elapsed_seconds,
            "elapsed_days": round(self.elapsed_seconds / 86400, 6),
            "properties": self.properties,
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

    Never raises on bad input: an unknown or unimplemented projection, a
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
    semantic = _SEMANTICS.get(projection)
    if spec is not None and semantic is None:
        diag.error(
            "PROJECTION.UNSUPPORTED_PROJECTION",
            f"projection {projection!r} has no registered semantics in schema v1",
        )

    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        diag.error(
            "PROJECTION.NAIVE_AS_OF",
            "as_of must be an explicit timezone-aware time",
            hint="use an explicit offset (e.g. 'Z' or '+09:00')",
        )
    else:
        result.as_of = as_of.astimezone(timezone.utc)

    if diag.has_errors or spec is None or semantic is None or result.as_of is None:
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
    result.definition_digest = _definition_digest(workspace, projection, spec.subject)
    result.candidates, result.inputs = semantic(
        workspace, store, spec.subject, result.as_of, diag
    )

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
    except (sqlite3.Error, OSError) as exc:
        diag.error(
            "PROJECTION.STORE_ERROR",
            f"cannot record projection run: {exc}",
        )

    try:
        trace.emit(
            "projection",
            "projection.completed",
            {
                "run_id": result.run_id,
                "projection": projection,
                "as_of": _iso_z(result.as_of),
                "definition_digest": result.definition_digest,
                "candidates": len(result.candidates),
                "inputs": len(result.inputs),
                "recorded": result.recorded,
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


def _completed_but_not_invoiced(
    workspace: Workspace,
    store: CanonicalStore,
    subject_entity: str,
    as_of: datetime,
    diag: Diagnostics,
) -> tuple[list[ProjectionCandidate], list[dict[str, Any]]]:
    """Subjects whose interval start event is more than two days old and whose
    interval end event has never been observed.

    The event pair comes from the workspace evaluation contract's
    ``interval`` — for the v0.1 job-invoicing loop that is JobCompleted →
    InvoiceIssued on the same Job subject.
    """
    interval = workspace.evaluation.interval
    start_event = workspace.domain.events.get(interval.start_event)
    end_event = workspace.domain.events.get(interval.end_event)
    if (
        start_event is None
        or end_event is None
        or start_event.subject != subject_entity
        or end_event.subject != subject_entity
    ):
        diag.error(
            "PROJECTION.SUBJECT_MISMATCH",
            f"projection subject {subject_entity!r} does not match the "
            f"evaluation interval subjects "
            f"({interval.start_event}: "
            f"{start_event.subject if start_event else 'unknown'}, "
            f"{interval.end_event}: "
            f"{end_event.subject if end_event else 'unknown'})",
        )
        return [], []

    candidates: list[ProjectionCandidate] = []
    inputs: list[dict[str, Any]] = []
    for subject_id in store.subject_ids_with_event(
        interval.start_event, subject_entity
    ):
        facts = store.events_for_subject(subject_entity, subject_id)
        starts = [f for f in facts if f.event == interval.start_event]
        ends = [f for f in facts if f.event == interval.end_event]
        first = min(starts, key=lambda f: f.occurred_at)
        entry: dict[str, Any] = {
            "subject_id": subject_id,
            "start_fact_keys": [f.fact_key for f in starts],
            "end_fact_keys": [f.fact_key for f in ends],
        }
        inputs.append(entry)
        if ends:
            entry["excluded"] = "interval end event exists"
            continue
        elapsed = (as_of - first.occurred_at).total_seconds()
        if elapsed < 0:
            entry["excluded"] = "future-dated start event"
            diag.warning(
                "PROJECTION.FUTURE_EVENT",
                f"{subject_entity}/{subject_id}: {interval.start_event} "
                f"occurred at {_iso_z(first.occurred_at)}, after as_of "
                f"{_iso_z(as_of)}",
            )
            continue
        if elapsed <= DELAY_THRESHOLD.total_seconds():
            entry["excluded"] = "elapsed within threshold"
            continue
        links = store.event_links(first.fact_key)
        entity = store.entity_state(subject_entity, subject_id)
        candidates.append(
            ProjectionCandidate(
                subject_entity=subject_entity,
                subject_id=subject_id,
                occurred_at=first.occurred_at,
                elapsed_seconds=elapsed,
                properties=entity["properties"] if entity else {},
                evidence={
                    "fact_key": first.fact_key,
                    "observation_ids": [obs for obs, _ in links],
                    "ingest_ids": sorted({ing for _, ing in links}),
                    "entity_observation_id": (
                        entity["last_observation_id"] if entity else None
                    ),
                },
            )
        )
    return candidates, inputs


_SEMANTICS: dict[
    str,
    Callable[[Workspace, CanonicalStore, str, datetime, Diagnostics], Any],
] = {
    "CompletedButNotInvoiced": _completed_but_not_invoiced,
}


def _definition_digest(workspace: Workspace, projection: str, subject: str) -> str:
    """Digest of everything that defines what this run computes.

    Covers the schema version, the projection declaration, the semantic
    identifier, the evaluation interval the semantic reads, and the fixed
    threshold — so a definition or contract edit changes the digest even
    when the candidate list happens to stay the same.
    """
    interval = workspace.evaluation.interval
    raw = json.dumps(
        {
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "projection": {"name": projection, "subject": subject},
            "semantics": "start-without-end-delayed",
            "interval": {
                "start_event": interval.start_event,
                "end_event": interval.end_event,
            },
            "threshold_seconds": DELAY_THRESHOLD.total_seconds(),
        },
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "DELAY_THRESHOLD",
    "ProjectionCandidate",
    "ProjectionResult",
    "get_projection_run",
    "run_projection",
]
