"""CSV/JSON snapshot ingestion with immutable observation evidence.

``ingest_snapshot`` reads one source snapshot in the format declared by the
workspace mapping (``mapping.source.format``), pushes every record through
the single normalization boundary in ``normalize.py``, resolves the mapping's
entity/event bindings, and records each record as an observation carrying:

- an observation id and the ingest run id,
- the source name and record index,
- the content digest of the snapshot bytes,
- a digest of the mapping definition plus the workspace schema version,
- the observation timestamp (when this run observed the data),
- the normalized record and its resolved entity/event values.

A record that produces any error diagnostic is rejected as a whole — no
partially mapped facts are emitted — but the rejected observation is still
recorded with its diagnostics, and every accepted/rejected record emits a
trace event. Accepted observations then persist canonical entities and
append-only events through ``canonical.py`` in a single transaction.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import ApplyResult, CanonicalStore, SqliteCanonicalStore
from .conditions import evaluate_condition
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR, EvidenceStore, FileEvidenceStore
from .model import FieldAssignment, Workspace
from .normalize import FAILED, Normalized, as_event_time, cast_scalar, normalize_record
from .schema import ErrorFn, err_at, warn_at
from .trace import TraceSink
from .workspace import SUPPORTED_SCHEMA_VERSION

MAX_SNAPSHOT_BYTES = 50_000_000

ACCEPTED = "accepted"
REJECTED = "rejected"


@dataclass(frozen=True)
class ResolvedEntity:
    """One entity binding resolved against a normalized record."""

    entity: str
    id: str
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "id": self.id,
            "properties": {k: _json_value(v) for k, v in self.properties.items()},
        }


@dataclass(frozen=True)
class ResolvedEvent:
    """One event binding whose ``when`` held, resolved against a record."""

    event: str
    subject_entity: str
    subject_id: str
    occurred_at: datetime
    observed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "subject": {"entity": self.subject_entity, "id": self.subject_id},
            "occurred_at": _iso_z(self.occurred_at),
            "observed_at": _iso_z(self.observed_at),
        }


@dataclass(frozen=True)
class Observation:
    """One source record observed during an ingest run.

    ``status`` is ``accepted`` when the record normalized and resolved without
    errors, else ``rejected``. Rejected observations keep the normalized
    record and the diagnostics explaining the rejection.
    """

    observation_id: str
    ingest_id: str
    source: str
    row: int
    status: str
    observed_at: datetime
    content_digest: str
    mapping_digest: str
    record: dict[str, Normalized]
    entities: list[ResolvedEntity]
    events: list[ResolvedEvent]
    diagnostics: list[Diagnostic]

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "ingest_id": self.ingest_id,
            "source": self.source,
            "row": self.row,
            "status": self.status,
            "observed_at": _iso_z(self.observed_at),
            "content_digest": self.content_digest,
            "mapping_digest": self.mapping_digest,
            "record": dict(self.record),
            "entities": [e.to_dict() for e in self.entities],
            "events": [e.to_dict() for e in self.events],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


@dataclass
class IngestResult:
    """Outcome of ``ingest_snapshot``: ordered diagnostics, the observations
    produced, and the evidence paths written."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    ingest_id: str | None = None
    source: str | None = None
    content_digest: str | None = None
    mapping_digest: str | None = None
    observed_at: datetime | None = None
    snapshot_path: Path | None = None
    observations_path: Path | None = None
    canonical: ApplyResult | None = None

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)

    @property
    def accepted(self) -> list[Observation]:
        return [o for o in self.observations if o.status == ACCEPTED]

    @property
    def rejected(self) -> list[Observation]:
        return [o for o in self.observations if o.status == REJECTED]


def ingest_snapshot(
    workspace: Workspace,
    snapshot: str | Path,
    *,
    observed_at: datetime | None = None,
    store: EvidenceStore | None = None,
    trace: TraceSink | None = None,
    canonical: CanonicalStore | None = None,
) -> IngestResult:
    """Ingest one CSV/JSON snapshot into immutable observation evidence.

    Never raises on bad input: unreadable files, parse errors, and malformed
    records all become diagnostics. The source file is never modified.
    """
    diag = Diagnostics()
    path = Path(snapshot)
    label = path.name
    result = IngestResult()

    if observed_at is None:
        observed_at = datetime.now(timezone.utc)
    elif observed_at.tzinfo is None or observed_at.tzinfo.utcoffset(observed_at) is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    else:
        observed_at = observed_at.astimezone(timezone.utc)
    result.observed_at = observed_at

    if store is None:
        store = FileEvidenceStore(Path(workspace.path) / RUNTIME_DIR)
    if trace is None:
        trace = store.trace_sink()
    # A failing trace sink becomes a diagnostic, never a mid-run crash.
    trace = _GuardedTrace(trace, diag, label)

    source = workspace.mapping.source
    result.source = source.name
    result.mapping_digest = _mapping_digest(workspace)

    content = _read_source(path, diag, label)
    if content is None:
        _emit_failed(trace, result, diag)
        result.diagnostics = diag.items
        return result

    result.content_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    try:
        result.snapshot_path = store.store_snapshot(
            content, result.content_digest, source.format
        )
    except OSError as exc:
        diag.error(
            "INGEST.STORE_ERROR",
            f"cannot store snapshot evidence: {exc.strerror or exc}",
            file=label,
        )

    text = content.decode("utf-8-sig")
    records = (
        _parse_csv(text, diag, label)
        if source.format == "csv"
        else _parse_json(text, diag, label)
    )
    if records is None:
        _emit_failed(trace, result, diag)
        result.diagnostics = diag.items
        return result

    if not records:
        diag.warning(
            "INGEST.EMPTY",
            "snapshot contains no records",
            file=label,
        )

    # The uuid component keeps the id unique per run even when a caller
    # replays the same snapshot with the same observed_at.
    result.ingest_id = (
        f"ing-{observed_at:%Y%m%dT%H%M%S%fZ}-{result.content_digest[7:15]}"
        f"-{uuid.uuid4().hex[:8]}"
    )

    for row, raw in records:
        observation = _process_record(
            workspace,
            raw,
            row=row,
            file_label=label,
            ingest_id=result.ingest_id,
            observed_at=observed_at,
            content_digest=result.content_digest,
            mapping_digest=result.mapping_digest,
            run_diag=diag,
        )
        result.observations.append(observation)
        _emit_observation(trace, observation)

    try:
        result.observations_path = store.write_observations(
            result.ingest_id,
            (json.dumps(o.to_dict(), sort_keys=True) for o in result.observations),
        )
    except OSError as exc:
        diag.error(
            "INGEST.STORE_ERROR",
            f"cannot write observations: {exc.strerror or exc}",
            file=label,
        )

    # Canonical facts must remain traceable to persisted evidence. If either
    # the snapshot bytes or the observations file failed to store, skip the
    # apply rather than commit facts whose observation ids cannot be
    # inspected — the INGEST.STORE_ERROR above already fails the run.
    if result.accepted and (
        result.snapshot_path is None or result.observations_path is None
    ):
        diag.warning(
            "CANON.SKIPPED",
            "canonical apply skipped because evidence persistence failed",
            file=label,
        )
    else:
        result.canonical = _apply_canonical(
            workspace, result, canonical, trace, diag, label
        )

    trace.emit(
        "ingest",
        "ingest.completed",
        {
            "ingest_id": result.ingest_id,
            "source": source.name,
            "content_digest": result.content_digest,
            "mapping_digest": result.mapping_digest,
            "observed_at": _iso_z(observed_at),
            "records": len(result.observations),
            "accepted": len(result.accepted),
            "rejected": len(result.rejected),
            "store_errors": sum(
                1 for d in diag.items if d.code == "INGEST.STORE_ERROR"
            ),
            "snapshot": str(result.snapshot_path) if result.snapshot_path else None,
            "observations": (
                str(result.observations_path) if result.observations_path else None
            ),
            "canonical": (
                {
                    "entities_upserted": result.canonical.entities_upserted,
                    "events_appended": result.canonical.events_appended,
                    "events_reobserved": result.canonical.events_reobserved,
                }
                if result.canonical is not None
                else None
            ),
        },
    )
    # Assign last so a trace failure on the completion event is still
    # reflected in the returned diagnostics.
    result.diagnostics = diag.items
    return result


def _apply_canonical(
    workspace: Workspace,
    result: IngestResult,
    canonical: CanonicalStore | None,
    trace: TraceSink,
    diag: Diagnostics,
    label: str,
) -> ApplyResult | None:
    """Persist one run's accepted observations into the canonical store.

    Only accepted observations write facts — a rejected record resolves no
    entities or events. The apply is one transaction: entity upserts, event
    inserts, and observation links commit together or roll back together,
    and a store failure is a diagnostic, not a mid-run crash.
    """
    if not result.accepted:
        return None
    if canonical is None:
        canonical = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    try:
        applied = canonical.apply(result.ingest_id or "", result.accepted)
    except Exception as exc:
        # The store is a pluggable boundary: any failure it raises — sqlite
        # errors, IO errors, or a custom implementation's own exceptions —
        # becomes a diagnostic, never a mid-run crash.
        message = exc.strerror if isinstance(exc, OSError) else str(exc)
        diag.error(
            "CANON.STORE_ERROR",
            f"cannot persist canonical state: {message or exc}",
            file=label,
        )
        trace.emit(
            "canonical",
            "canonical.failed",
            {"ingest_id": result.ingest_id, "error": str(exc)},
        )
        return None
    for event in applied.appended:
        trace.emit(
            "canonical",
            "canonical.event_recorded",
            {
                "ingest_id": result.ingest_id,
                "fact_key": event.fact_key,
                "event": event.event,
                "subject": {
                    "entity": event.subject_entity,
                    "id": event.subject_id,
                },
                "occurred_at": _iso_z(event.occurred_at),
                "observation_id": event.first_observation_id,
            },
        )
    trace.emit(
        "canonical",
        "canonical.applied",
        {
            "ingest_id": result.ingest_id,
            "observations": len(result.accepted),
            "entities_upserted": applied.entities_upserted,
            "events_appended": applied.events_appended,
            "events_reobserved": applied.events_reobserved,
        },
    )
    return applied


def _process_record(
    workspace: Workspace,
    raw: dict[Any, Any] | None,
    *,
    row: int,
    file_label: str,
    ingest_id: str,
    observed_at: datetime,
    content_digest: str,
    mapping_digest: str,
    run_diag: Diagnostics,
) -> Observation:
    """Normalize and resolve one record into an accepted/rejected observation."""
    rdiag = Diagnostics()
    err = err_at(rdiag, file_label)
    warn = warn_at(rdiag, file_label)

    entities: list[ResolvedEntity] = []
    events: list[ResolvedEvent] = []
    if raw is None:
        err(
            "INGEST.INVALID_ROW",
            f"record[{row}]",
            "record is not a flat mapping of fields",
        )
        record: dict[str, Normalized] = {}
    else:
        if None in raw:
            err(
                "INGEST.INVALID_ROW",
                f"record[{row}]",
                "row has more cells than the header declares",
            )
        record = normalize_record(raw, err, row)
        if not rdiag.has_errors:
            entities, events = _resolve(
                workspace, record, err, warn, row, observed_at
            )

    status = REJECTED if rdiag.has_errors else ACCEPTED
    if status == REJECTED:
        # A rejected record emits no partially mapped facts.
        entities, events = [], []
    run_diag.items.extend(rdiag.items)
    observation_id = hashlib.sha256(f"{ingest_id}:{row}".encode()).hexdigest()[:16]
    return Observation(
        observation_id=f"obs-{observation_id}",
        ingest_id=ingest_id,
        source=workspace.mapping.source.name,
        row=row,
        status=status,
        observed_at=observed_at,
        content_digest=content_digest,
        mapping_digest=mapping_digest,
        record=record,
        entities=entities,
        events=events,
        diagnostics=list(rdiag.items),
    )


def _resolve(
    workspace: Workspace,
    record: dict[str, Normalized],
    err: ErrorFn,
    warn: ErrorFn,
    row: int,
    observed_at: datetime,
) -> tuple[list[ResolvedEntity], list[ResolvedEvent]]:
    """Resolve the mapping's bindings against a normalized record."""
    domain = workspace.domain
    tz = domain.policies.event_times.date_only_timezone

    entities: list[ResolvedEntity] = []
    for binding in workspace.mapping.entities:
        entity_id = _resolve_id(
            binding.id, record, err, warn, row, f"entity {binding.entity!r} id"
        )
        if entity_id is None:
            continue
        entity_type = domain.entities.get(binding.entity)
        properties: dict[str, Any] = {}
        for name, assignment in binding.properties.items():
            path = _assign_path(row, assignment)
            value = _resolve_value(assignment, record, err, path, tz)
            if value is FAILED:
                continue
            spec = entity_type.properties.get(name) if entity_type else None
            if value is None and spec is not None and spec.required:
                err(
                    "INGEST.MISSING_REQUIRED",
                    path,
                    f"required property {name!r} of entity {binding.entity!r} "
                    "is absent or blank",
                )
                continue
            properties[name] = value
        entities.append(
            ResolvedEntity(entity=binding.entity, id=entity_id, properties=properties)
        )

    events: list[ResolvedEvent] = []
    for binding in workspace.mapping.events:
        if binding.when is not None and not evaluate_condition(binding.when, record):
            continue
        subject_id = _resolve_id(
            binding.subject.id,
            record,
            err,
            warn,
            row,
            f"event {binding.event!r} subject {binding.subject.entity!r} id",
        )
        occurred = _resolve_value(
            binding.occurred_at, record, err, _assign_path(row, binding.occurred_at), tz
        )
        if subject_id is None or occurred is FAILED:
            continue
        if occurred is None:
            err(
                "INGEST.MISSING_TIME",
                _assign_path(row, binding.occurred_at),
                f"event {binding.event!r} applies but occurred_at is absent or blank",
            )
            continue
        occurred_at = as_event_time(occurred, tz)

        event_observed = None
        if binding.observed_at is not None:
            event_observed = _resolve_value(
                binding.observed_at,
                record,
                err,
                _assign_path(row, binding.observed_at),
                tz,
            )
            if event_observed is FAILED:
                continue
        events.append(
            ResolvedEvent(
                event=binding.event,
                subject_entity=binding.subject.entity,
                subject_id=subject_id,
                occurred_at=occurred_at,
                # A source-reported observation time wins; otherwise the event
                # was observed when this run ingested the record.
                observed_at=(
                    as_event_time(event_observed, tz)
                    if event_observed is not None
                    else observed_at
                ),
            )
        )
    return entities, events


def _resolve_id(
    assignment: FieldAssignment,
    record: dict[str, Normalized],
    err: ErrorFn,
    warn: ErrorFn,
    row: int,
    what: str,
) -> str | None:
    """Resolve an entity/subject id: always a non-empty string, never cast.

    Ids are opaque and kept verbatim — a whitespace-padded id is a different
    id. Because such padding is almost always a data accident that would
    duplicate an entity downstream, it emits ``INGEST.SUSPICIOUS_ID``.
    """
    path = _assign_path(row, assignment)
    raw = record.get(assignment.field) if assignment.field is not None else assignment.literal
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        err("INGEST.MISSING_ID", path, f"{what}: {_assign_label(assignment)} is absent or blank")
        return None
    if not isinstance(raw, str):
        err(
            "INGEST.INVALID_ID",
            path,
            f"{what}: id must be a string, got {_kind(raw)}",
            hint="entity ids are opaque strings and are never coerced",
        )
        return None
    if raw != raw.strip():
        warn(
            "INGEST.SUSPICIOUS_ID",
            path,
            f"{what}: id {raw!r} has leading/trailing whitespace; kept verbatim",
            hint="whitespace-padded ids are distinct ids and will not deduplicate",
        )
    return raw


def _resolve_value(
    assignment: FieldAssignment,
    record: dict[str, Normalized],
    err,
    path: str,
    date_only_timezone: str,
) -> Any:
    """Resolve one assignment to a typed value (or ``None``/``FAILED``)."""
    raw = record.get(assignment.field) if assignment.field is not None else assignment.literal
    if assignment.cast is not None:
        return cast_scalar(
            raw, assignment.cast, err, path, date_only_timezone=date_only_timezone
        )
    if raw is None:
        return None
    if not isinstance(raw, str):
        err(
            "INGEST.INVALID_VALUE",
            path,
            f"expected a string value, got {_kind(raw)}",
        )
        return FAILED
    return raw


def _assign_path(row: int, assignment: FieldAssignment) -> str:
    if assignment.field is not None:
        return f"record[{row}].{assignment.field}"
    return f"record[{row}]"


def _assign_label(assignment: FieldAssignment) -> str:
    if assignment.field is not None:
        return f"field {assignment.field!r}"
    return f"literal {assignment.literal!r}"


def _read_source(path: Path, diag: Diagnostics, label: str) -> bytes | None:
    """Read snapshot bytes; every failure becomes a diagnostic."""
    try:
        is_file = path.is_file()
    except (OSError, ValueError):
        is_file = False
    if not is_file:
        diag.error(
            "INGEST.UNREADABLE_SOURCE",
            f"snapshot path is not a file: {path}",
            file=label,
        )
        return None

    try:
        size = path.stat().st_size
    except OSError as exc:
        diag.error(
            "INGEST.UNREADABLE_SOURCE",
            f"cannot stat file: {exc.strerror or exc}",
            file=label,
        )
        return None
    if size > MAX_SNAPSHOT_BYTES:
        diag.error(
            "INGEST.SOURCE_TOO_LARGE",
            f"snapshot is {size} bytes; limit is {MAX_SNAPSHOT_BYTES}",
            file=label,
        )
        return None

    try:
        content = path.read_bytes()
    except OSError as exc:
        diag.error(
            "INGEST.UNREADABLE_SOURCE",
            f"cannot read file: {exc.strerror or exc}",
            file=label,
        )
        return None
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        diag.error(
            "INGEST.ENCODING_ERROR",
            f"snapshot is not valid UTF-8: {exc}",
            file=label,
        )
        return None
    return content


def _parse_csv(
    text: str, diag: Diagnostics, label: str
) -> list[tuple[int, dict[Any, Any] | None]] | None:
    """Parse CSV text into (row, raw record) pairs; None on source errors."""
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fieldnames = reader.fieldnames
        if not fieldnames:
            diag.error(
                "INGEST.INVALID_SOURCE",
                "CSV snapshot has no header row",
                file=label,
            )
            return None
        duplicates = sorted({n for n in fieldnames if fieldnames.count(n) > 1})
        if duplicates:
            diag.error(
                "INGEST.DUPLICATE_COLUMN",
                f"CSV header declares duplicate columns: {', '.join(duplicates)}",
                file=label,
            )
            return None
        return [(index, row) for index, row in enumerate(reader, start=1)]
    except csv.Error as exc:
        diag.error(
            "INGEST.INVALID_SOURCE",
            f"invalid CSV: {exc}",
            file=label,
        )
        return None


def _reject_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON constant {value!r}")


def _parse_json(
    text: str, diag: Diagnostics, label: str
) -> list[tuple[int, dict[Any, Any] | None]] | None:
    """Parse a JSON snapshot (a top-level array of flat objects)."""
    try:
        document = json.loads(text, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        diag.error(
            "INGEST.INVALID_SOURCE",
            f"invalid JSON: {exc}",
            file=label,
        )
        return None
    if not isinstance(document, list):
        diag.error(
            "INGEST.INVALID_SOURCE",
            "JSON snapshot must be a top-level array of records",
            file=label,
        )
        return None
    return [
        (index, item if isinstance(item, dict) else None)
        for index, item in enumerate(document, start=1)
    ]


def _mapping_digest(workspace: Workspace) -> str:
    """Digest of the mapping definition plus the workspace schema version.

    Computed from the loaded mapping object — the definition the records are
    actually resolved with — so a concurrent edit of ``mapping.yaml`` cannot
    make the recorded digest point at a different mapping.
    """
    raw = json.dumps(
        dataclasses.asdict(workspace.mapping), sort_keys=True, default=str
    ).encode("utf-8")
    payload = f"schema_version={SUPPORTED_SCHEMA_VERSION}\n".encode("utf-8") + raw
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class _GuardedTrace:
    """Trace sink wrapper: a sink ``OSError`` becomes one run diagnostic.

    Trace output is evidence persistence like the snapshot and observation
    files; a failing sink must not crash the run after records are processed.
    The failure is reported once — later events are still attempted so a
    partially-recovering sink keeps recording.
    """

    def __init__(self, sink: TraceSink, diag: Diagnostics, label: str) -> None:
        self._sink = sink
        self._diag = diag
        self._label = label
        self._reported = False

    def emit(self, stage: str, kind: str, data: dict[str, Any]) -> None:
        try:
            self._sink.emit(stage, kind, data)
        except OSError as exc:
            if not self._reported:
                self._reported = True
                self._diag.error(
                    "INGEST.STORE_ERROR",
                    f"cannot write trace: {exc.strerror or exc}",
                    file=self._label,
                )


def _emit_observation(trace: TraceSink, observation: Observation) -> None:
    data: dict[str, Any] = {
        "observation_id": observation.observation_id,
        "ingest_id": observation.ingest_id,
        "source": observation.source,
        "row": observation.row,
        "observed_at": _iso_z(observation.observed_at),
    }
    if observation.status == ACCEPTED:
        data["entities"] = len(observation.entities)
        data["events"] = len(observation.events)
        trace.emit("ingest", "observation.accepted", data)
    else:
        data["codes"] = [d.code for d in observation.diagnostics]
        trace.emit("ingest", "observation.rejected", data)


def _emit_failed(trace: TraceSink, result: IngestResult, diag: Diagnostics) -> None:
    trace.emit(
        "ingest",
        "ingest.failed",
        {
            "source": result.source,
            "mapping_digest": result.mapping_digest,
            "codes": [d.code for d in diag.errors],
        },
    )


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso_z(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return f"number {value!r}"
    if isinstance(value, str):
        return f"string {value!r}"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return type(value).__name__


__all__ = [
    "ACCEPTED",
    "MAX_SNAPSHOT_BYTES",
    "REJECTED",
    "IngestResult",
    "Observation",
    "ResolvedEntity",
    "ResolvedEvent",
    "ingest_snapshot",
]
