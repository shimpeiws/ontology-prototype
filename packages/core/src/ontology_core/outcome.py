"""Outcome evaluation: baseline/after cohorts over canonical intervals.

The workspace evaluation contract (``evaluation.yaml``, validated in
evaluation.py) declares an interval — a start event and an end event on
one subject — plus the aggregation, a declared baseline, a strict target,
and guardrails. ``run_evaluation`` pairs those interval events per
canonical subject at an explicit ``as_of``, splits subjects into a
baseline and an after cohort at an explicit ``boundary``, and reports
aggregates, pending and invalid intervals, the target verdict, and
guardrail verdicts computed from explicit evidence counts — then freezes
the full report into the append-only ``evaluation_runs`` table.

Cohort and pairing rules
------------------------

Both ``as_of`` and ``boundary`` are explicit, aware datetimes — never
defaults and never guessed. Cohort membership is decided by the
interval's *start* event alone: a subject whose earliest visible start
event occurred before ``boundary`` belongs to the baseline cohort,
otherwise to the after cohort. Only events with ``occurred_at <= as_of``
are visible to a run; later facts are listed in the inputs as future
events and do not close an interval.

Within a cohort, each subject resolves to exactly one disposition:

- ``paired`` — one start event and at least one end event at or after it;
  the earliest closing end pairs the interval. Additional ends are
  evidence anomalies (``EVALUATION.MULTIPLE_ENDS``) recorded on the pair.
- ``pending`` — one start event and no visible end event; the subject is
  reported with its elapsed time, never silently omitted.
- ``invalid`` — every end event precedes the start event, so the only
  pairings would have negative duration (``EVALUATION.NEGATIVE_INTERVAL``).
- ``ambiguous`` — the interval cannot be paired uniquely: more than one
  start event, or end events on both sides of the start
  (``EVALUATION.AMBIGUOUS_INTERVAL``). Ambiguous subjects are excluded
  from aggregates.
- ``unpaired_end`` — an end event with no visible start event
  (``EVALUATION.END_WITHOUT_START``); it belongs to no cohort.

A baseline pair whose end occurred at or after the boundary is flagged
``closes_after_boundary`` — its start places it in the baseline cohort
even though the interval closed later.

Verdicts
--------

``change`` is ``baseline - after`` in the contract unit; for the v0.1
``duration`` metric a positive value is an improvement (lower is better).
The target compares the after aggregate with the contract comparator —
``lt`` is strict, so a median exactly on the target is a miss. Guardrails
evaluate per cohort from explicit counts and denominators; a missing
denominator is ``unavailable``, never ``passed``. Overall success requires
the target attained and every guardrail passed; a missed target or failed
guardrail is ``not_met``; anything else is ``unverified``.

The report records an observed difference between time-based cohorts.
The boundary is caller-supplied data — typically the intervention time —
but the evaluator never ties it to a proposal or execution and makes no
causal claim.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .canonical import CanonicalStore, SqliteCanonicalStore
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR
from .model import GuardrailSpec, Workspace
from .trace import JsonlTraceSink, TraceSink
from .workspace import SUPPORTED_SCHEMA_VERSION

# Version of the cohort/pairing semantics. Bump when the selection rules
# change so previously frozen reports carry a different contract digest.
_SEMANTIC_VERSION = 1

# Seconds in one declared contract unit; durations are reported in the
# contract's unit rather than raw seconds.
_UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}

# Evidence convention of the registered ``invoice_error_rate`` guardrail
# semantics: Invoice entities linked to a cohort subject through the
# ``job`` ref, with the boolean ``error`` flag as the error signal. These
# names are the v0.1 convention — a workspace whose domain uses different
# names simply has no evidence and reports the guardrail unavailable.
_INVOICE_ENTITY = "Invoice"
_INVOICE_JOB_REF = "job"
_INVOICE_ERROR_FLAG = "error"

_COMPARATORS: dict[str, Callable[[float, float], bool]] = {
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
}

_DISPOSITIONS = ("paired", "pending", "invalid", "ambiguous")


@dataclass
class EvaluationResult:
    """Outcome of ``run_evaluation``: diagnostics, the run identity, and
    the frozen report payload."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    run_id: str | None = None
    evaluation: str | None = None
    as_of: datetime | None = None
    boundary: datetime | None = None
    contract_digest: str | None = None
    report: dict[str, Any] | None = None
    recorded: bool = False

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


def run_evaluation(
    workspace: Workspace,
    *,
    as_of: datetime,
    boundary: datetime,
    store: CanonicalStore | None = None,
    trace: TraceSink | None = None,
) -> EvaluationResult:
    """Evaluate baseline/after cohorts of the contract interval and
    freeze the report.

    Never raises on bad input: naive or ordered-inconsistent times,
    unknown subjects, and store failures all become diagnostics. A run
    that produced errors is never frozen — a recorded run always means
    "evaluated successfully".
    """
    diag = Diagnostics()
    evaluation = workspace.evaluation
    result = EvaluationResult(evaluation=evaluation.name)

    for name, value, code in (
        ("as_of", as_of, "EVALUATION.NAIVE_AS_OF"),
        ("boundary", boundary, "EVALUATION.NAIVE_BOUNDARY"),
    ):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            diag.error(
                code,
                f"{name} must be an explicit timezone-aware time",
                hint="use an explicit offset (e.g. 'Z' or '+09:00')",
            )
    if not diag.has_errors:
        result.as_of = as_of.astimezone(timezone.utc)
        result.boundary = boundary.astimezone(timezone.utc)
        if result.boundary > result.as_of:
            diag.error(
                "EVALUATION.BOUNDARY_AFTER_AS_OF",
                "cohort boundary must not be later than as_of",
            )
    if diag.has_errors or result.as_of is None or result.boundary is None:
        result.diagnostics = diag.items
        return result

    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    if trace is None:
        trace = JsonlTraceSink(Path(workspace.path) / RUNTIME_DIR / "trace.jsonl")

    created_at = datetime.now(timezone.utc)
    result.run_id = (
        f"evl-{created_at:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}"
    )
    result.contract_digest = _contract_digest(workspace)
    try:
        result.report = _build_report(
            workspace,
            store,
            result.as_of,
            result.boundary,
            result.contract_digest,
            diag,
        )
    except Exception as exc:
        # Canonical reads go through the pluggable store boundary: a
        # locked or corrupt database (or a custom store's own exception)
        # becomes a diagnostic, never a traceback through the CLI.
        diag.error(
            "EVALUATION.STORE_ERROR",
            f"cannot read canonical state: {exc}",
        )

    # A run that produced errors is not frozen into evaluation_runs: a
    # recorded run must always mean "evaluated successfully", so a
    # failed evaluation can never masquerade as an empty report.
    if result.report is not None and not diag.has_errors:
        try:
            store.record_evaluation_run(
                run_id=result.run_id,
                evaluation=evaluation.name,
                as_of=result.as_of,
                boundary=result.boundary,
                created_at=created_at,
                contract_digest=result.contract_digest,
                report=result.report,
            )
            result.recorded = True
        except Exception as exc:
            diag.error(
                "EVALUATION.STORE_ERROR",
                f"cannot record evaluation run: {exc}",
            )

    try:
        overall = (
            result.report["overall"]["status"] if result.report is not None else None
        )
        trace.emit(
            "evaluation",
            "evaluation.failed" if diag.has_errors else "evaluation.completed",
            {
                "run_id": result.run_id,
                "evaluation": evaluation.name,
                "as_of": _iso_z(result.as_of),
                "cohort_boundary": _iso_z(result.boundary),
                "contract_digest": result.contract_digest,
                "overall": overall,
                "recorded": result.recorded,
                "errors": [
                    d.code for d in diag.items if d.severity == "error"
                ],
            },
        )
    except OSError as exc:
        diag.warning(
            "EVALUATION.TRACE_ERROR",
            f"cannot write trace: {exc.strerror or exc}",
        )
    result.diagnostics = diag.items
    return result


def get_evaluation_run(
    workspace: Workspace, run_id: str, *, store: CanonicalStore | None = None
) -> dict[str, Any] | None:
    """Read back a frozen evaluation report by run id."""
    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    return store.evaluation_run(run_id)


def _build_report(
    workspace: Workspace,
    store: CanonicalStore,
    as_of: datetime,
    boundary: datetime,
    contract_digest: str,
    diag: Diagnostics,
) -> dict[str, Any] | None:
    evaluation = workspace.evaluation
    interval = evaluation.interval
    start_type = workspace.domain.events.get(interval.start_event)
    if start_type is None:
        # Unreachable through load_workspace — an unknown interval event
        # already fails contract validation — but a directly constructed
        # Workspace gets a diagnostic instead of a KeyError.
        diag.error(
            "EVALUATION.UNKNOWN_EVENT",
            f"interval start event {interval.start_event!r} is not "
            "declared in the domain",
        )
        return None
    subject_entity = start_type.subject
    unit_seconds = _UNIT_SECONDS[evaluation.unit]

    subject_ids = sorted(
        set(store.subject_ids_with_event(interval.start_event, subject_entity))
        | set(store.subject_ids_with_event(interval.end_event, subject_entity))
    )

    cohorts: dict[str, dict[str, list[dict[str, Any]]]] = {
        name: {kind: [] for kind in _DISPOSITIONS}
        for name in ("baseline", "after")
    }
    cohort_subjects: dict[str, set[str]] = {"baseline": set(), "after": set()}
    unpaired_ends: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []

    for subject_id in subject_ids:
        facts = store.events_for_subject(subject_entity, subject_id)
        starts = [f for f in facts if f.event == interval.start_event]
        ends = [f for f in facts if f.event == interval.end_event]
        visible_starts = [f for f in starts if f.occurred_at <= as_of]
        visible_ends = [f for f in ends if f.occurred_at <= as_of]
        future_starts = [f for f in starts if f.occurred_at > as_of]
        future_ends = [f for f in ends if f.occurred_at > as_of]

        entry: dict[str, Any] = {
            "subject_id": subject_id,
            "start_fact_keys": [f.fact_key for f in visible_starts],
            "end_fact_keys": [f.fact_key for f in visible_ends],
        }
        if future_starts:
            entry["future_start_fact_keys"] = [
                f.fact_key for f in future_starts
            ]
        if future_ends:
            entry["future_end_fact_keys"] = [f.fact_key for f in future_ends]
        inputs.append(entry)

        subject = f"{subject_entity}/{subject_id}"
        if not visible_starts:
            if visible_ends:
                entry["disposition"] = "unpaired_end"
                unpaired_ends.append(
                    {
                        "subject_id": subject_id,
                        "end_fact_keys": entry["end_fact_keys"],
                    }
                )
                diag.warning(
                    "EVALUATION.END_WITHOUT_START",
                    f"{subject}: {interval.end_event} exists without a "
                    f"{interval.start_event} at or before as_of",
                )
            else:
                entry["disposition"] = "excluded"
                if future_starts:
                    entry["excluded"] = "future-dated start event"
                    first_future = min(
                        future_starts,
                        key=lambda f: (f.occurred_at, f.fact_key),
                    )
                    diag.warning(
                        "EVALUATION.FUTURE_EVENT",
                        f"{subject}: {interval.start_event} occurred at "
                        f"{_iso_z(first_future.occurred_at)}, after as_of "
                        f"{_iso_z(as_of)}",
                    )
                else:
                    entry["excluded"] = "no events at or before as_of"
            continue

        first_start = min(
            visible_starts, key=lambda f: (f.occurred_at, f.fact_key)
        )
        cohort = (
            "baseline" if first_start.occurred_at < boundary else "after"
        )
        entry["cohort"] = cohort
        cohort_subjects[cohort].add(subject_id)

        if len(visible_starts) > 1:
            entry["disposition"] = "ambiguous"
            _ambiguous(
                cohorts,
                cohort,
                subject_id,
                "multiple start events; the interval cannot be paired "
                "uniquely",
                entry["start_fact_keys"],
                entry["end_fact_keys"],
            )
            diag.warning(
                "EVALUATION.AMBIGUOUS_INTERVAL",
                f"{subject}: {len(visible_starts)} {interval.start_event} "
                "facts exist; the interval is excluded from aggregates",
            )
            continue

        start = visible_starts[0]
        earlier_ends = [
            f for f in visible_ends if f.occurred_at < start.occurred_at
        ]
        closing_ends = [
            f for f in visible_ends if f.occurred_at >= start.occurred_at
        ]
        if earlier_ends and closing_ends:
            entry["disposition"] = "ambiguous"
            _ambiguous(
                cohorts,
                cohort,
                subject_id,
                "end events on both sides of the start event",
                entry["start_fact_keys"],
                entry["end_fact_keys"],
            )
            diag.warning(
                "EVALUATION.AMBIGUOUS_INTERVAL",
                f"{subject}: {interval.end_event} facts occur both before "
                f"and after {interval.start_event}; the interval is "
                "excluded from aggregates",
            )
        elif earlier_ends:
            entry["disposition"] = "invalid"
            cohorts[cohort]["invalid"].append(
                {
                    "subject_id": subject_id,
                    "reason": "every end event precedes the start event; "
                    "pairing would produce a negative interval",
                    "start_fact_key": start.fact_key,
                    "end_fact_keys": entry["end_fact_keys"],
                }
            )
            diag.warning(
                "EVALUATION.NEGATIVE_INTERVAL",
                f"{subject}: {interval.end_event} occurred before "
                f"{interval.start_event}; the interval is excluded "
                "from aggregates",
            )
        elif not closing_ends:
            entry["disposition"] = "pending"
            elapsed = (as_of - start.occurred_at).total_seconds()
            cohorts[cohort]["pending"].append(
                {
                    "subject_id": subject_id,
                    "start_fact_key": start.fact_key,
                    "started_at": _iso_z(start.occurred_at),
                    "elapsed_seconds": elapsed,
                    "elapsed": elapsed / unit_seconds,
                }
            )
        else:
            closer = min(
                closing_ends, key=lambda f: (f.occurred_at, f.fact_key)
            )
            extras = [
                f.fact_key
                for f in closing_ends
                if f.fact_key != closer.fact_key
            ]
            if extras:
                diag.warning(
                    "EVALUATION.MULTIPLE_ENDS",
                    f"{subject}: {len(closing_ends)} {interval.end_event} "
                    "facts could close the interval; the earliest pairs it",
                )
            duration = (
                closer.occurred_at - start.occurred_at
            ).total_seconds()
            pair: dict[str, Any] = {
                "subject_id": subject_id,
                "start_fact_key": start.fact_key,
                "end_fact_key": closer.fact_key,
                "started_at": _iso_z(start.occurred_at),
                "ended_at": _iso_z(closer.occurred_at),
                "duration_seconds": duration,
                "duration": duration / unit_seconds,
            }
            if extras:
                pair["extra_end_fact_keys"] = extras
            if cohort == "baseline" and closer.occurred_at >= boundary:
                # The start places the pair in the baseline cohort even
                # though the interval closed after the boundary.
                pair["closes_after_boundary"] = True
            cohorts[cohort]["paired"].append(pair)
            entry["disposition"] = "paired"

    report_cohorts: dict[str, dict[str, Any]] = {}
    for name in ("baseline", "after"):
        data = cohorts[name]
        durations = [p["duration"] for p in data["paired"]]
        report_cohorts[name] = {
            "paired": data["paired"],
            "pending": data["pending"],
            "invalid": data["invalid"],
            "ambiguous": data["ambiguous"],
            "counts": {
                kind: len(data[kind]) for kind in _DISPOSITIONS
            },
            "sample": len(durations),
            "aggregate": _aggregate(durations, evaluation.aggregation),
        }

    baseline_agg = report_cohorts["baseline"]["aggregate"]
    after_agg = report_cohorts["after"]["aggregate"]
    if baseline_agg is not None and after_agg is not None:
        delta = baseline_agg - after_agg
        change: dict[str, Any] = {
            "status": "measured",
            "value": delta,
            "direction": (
                "improvement"
                if delta > 0
                else "regression"
                if delta < 0
                else "unchanged"
            ),
        }
    else:
        missing = [
            name
            for name in ("baseline", "after")
            if report_cohorts[name]["aggregate"] is None
        ]
        change = {
            "status": "unavailable",
            "reason": "no measured intervals in the "
            + " and ".join(missing)
            + " cohort",
        }

    target = evaluation.target
    if after_agg is None:
        target_report: dict[str, Any] = {
            "comparator": target.comparator,
            "value": target.value,
            "status": "unverified",
            "reason": "the after cohort has no measured intervals",
        }
    else:
        attained = _COMPARATORS[target.comparator](after_agg, target.value)
        target_report = {
            "comparator": target.comparator,
            "value": target.value,
            "status": "attained" if attained else "missed",
        }

    guardrail_reports = [
        _evaluate_guardrail(workspace, store, spec, cohort_subjects, diag)
        for spec in evaluation.guardrails
    ]

    statuses = [target_report["status"]] + [
        g["status"] for g in guardrail_reports
    ]
    if "missed" in statuses or "failed" in statuses:
        overall = "not_met"
    elif target_report["status"] == "attained" and all(
        g["status"] == "passed" for g in guardrail_reports
    ):
        overall = "success"
    else:
        overall = "unverified"

    return {
        "evaluation": evaluation.name,
        "contract_digest": contract_digest,
        "as_of": _iso_z(as_of),
        "cohort_boundary": _iso_z(boundary),
        "interval": {
            "start_event": interval.start_event,
            "end_event": interval.end_event,
            "subject": subject_entity,
        },
        "metric": evaluation.metric,
        "aggregation": evaluation.aggregation,
        "unit": evaluation.unit,
        "declared_baseline": evaluation.baseline,
        "cohorts": report_cohorts,
        "unpaired_ends": unpaired_ends,
        "change": change,
        "target": target_report,
        "guardrails": guardrail_reports,
        "overall": {"status": overall},
        "inputs": inputs,
        "note": (
            "Cohorts are assigned by when each interval's start event "
            "occurred relative to the cohort boundary. This report "
            "records an observed difference between cohorts; it does "
            "not establish that any intervention caused it."
        ),
    }


def _ambiguous(
    cohorts: dict[str, dict[str, list[dict[str, Any]]]],
    cohort: str,
    subject_id: str,
    reason: str,
    start_fact_keys: list[str],
    end_fact_keys: list[str],
) -> None:
    cohorts[cohort]["ambiguous"].append(
        {
            "subject_id": subject_id,
            "reason": reason,
            "start_fact_keys": start_fact_keys,
            "end_fact_keys": end_fact_keys,
        }
    )


def _aggregate(durations: list[float], aggregation: str) -> float | None:
    if aggregation == "count":
        return float(len(durations))
    if not durations:
        return None
    if aggregation == "median":
        return float(statistics.median(durations))
    if aggregation == "mean":
        return statistics.fmean(durations)
    if aggregation == "min":
        return min(durations)
    if aggregation == "max":
        return max(durations)
    return None  # unreachable: the contract allowlist is validated


def _evaluate_guardrail(
    workspace: Workspace,
    store: CanonicalStore,
    spec: GuardrailSpec,
    cohort_subjects: dict[str, set[str]],
    diag: Diagnostics,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "name": spec.name,
        "metric": spec.metric,
        "comparator": spec.comparator,
    }
    metric = _GUARDRAIL_METRICS.get(spec.metric)
    if metric is None:
        report["status"] = "unavailable"
        report["reason"] = (
            f"no registered semantics for metric {spec.metric!r}"
        )
        diag.warning(
            "EVALUATION.UNSUPPORTED_GUARDRAIL",
            f"guardrail {spec.name!r} uses metric {spec.metric!r}, which "
            "has no registered semantics",
        )
        return report

    evidence = metric(workspace, store, cohort_subjects)
    report["evidence"] = evidence
    baseline_rate = evidence["baseline"]["rate"]
    after_rate = evidence["after"]["rate"]

    if spec.comparator == "not_increase":
        if baseline_rate is None or after_rate is None:
            missing = [
                name
                for name, rate in (
                    ("baseline", baseline_rate),
                    ("after", after_rate),
                )
                if rate is None
            ]
            report["status"] = "unavailable"
            report["reason"] = (
                "no invoice-error evidence for the "
                + " and ".join(missing)
                + " cohort"
            )
        else:
            report["status"] = (
                "passed" if after_rate <= baseline_rate else "failed"
            )
    else:
        if after_rate is None:
            report["status"] = "unavailable"
            report["reason"] = (
                "no invoice-error evidence for the after cohort"
            )
        else:
            report["status"] = (
                "passed"
                if _COMPARATORS[spec.comparator](after_rate, spec.value)
                else "failed"
            )
    if report["status"] == "unavailable":
        diag.warning(
            "EVALUATION.GUARDRAIL_UNAVAILABLE",
            f"guardrail {spec.name!r} cannot be evaluated: "
            f"{report['reason']}",
        )
    return report


def _invoice_error_rate(
    workspace: Workspace,
    store: CanonicalStore,
    cohort_subjects: dict[str, set[str]],
) -> dict[str, Any]:
    """Explicit error counts and denominators for the registered
    ``invoice_error_rate`` guardrail.

    The denominator of a cohort is the number of Invoice entities linked
    (through the ``job`` ref) to a subject of that cohort and carrying a
    boolean ``error`` value; the numerator is those flagged ``true``.
    Invoices without a boolean flag, and invoices whose job is in no
    cohort, are reported as evidence gaps — never silently folded into
    the rate.
    """
    buckets: dict[str, dict[str, Any]] = {
        name: {"errors": 0, "invoices": 0, "missing_error": 0, "entity_ids": []}
        for name in ("baseline", "after")
    }
    unmatched = 0
    for entity in store.entities_of_type(_INVOICE_ENTITY):
        properties = entity["properties"]
        job = properties.get(_INVOICE_JOB_REF)
        cohort = next(
            (
                name
                for name in ("baseline", "after")
                if isinstance(job, str) and job in cohort_subjects[name]
            ),
            None,
        )
        if cohort is None:
            unmatched += 1
            continue
        bucket = buckets[cohort]
        bucket["entity_ids"].append(entity["entity_id"])
        error = properties.get(_INVOICE_ERROR_FLAG)
        if isinstance(error, bool):
            bucket["invoices"] += 1
            if error:
                bucket["errors"] += 1
        else:
            bucket["missing_error"] += 1

    evidence: dict[str, Any] = {
        "convention": {
            "entity": _INVOICE_ENTITY,
            "job_ref": _INVOICE_JOB_REF,
            "error_flag": _INVOICE_ERROR_FLAG,
        },
        "unmatched_invoices": unmatched,
    }
    for name in ("baseline", "after"):
        bucket = buckets[name]
        evidence[name] = {
            "invoices": bucket["invoices"],
            "errors": bucket["errors"],
            "rate": (
                bucket["errors"] / bucket["invoices"]
                if bucket["invoices"]
                else None
            ),
            "invoices_missing_error": bucket["missing_error"],
            "entity_ids": sorted(bucket["entity_ids"]),
        }
    return evidence


# Registered guardrail metric semantics, keyed by the contract's metric
# name — the same pattern as projection _SEMANTICS. A contract that names
# an unregistered metric evaluates its guardrail as unavailable.
_GUARDRAIL_METRICS: dict[
    str,
    Callable[[Workspace, CanonicalStore, dict[str, set[str]]], dict[str, Any]],
] = {
    "invoice_error_rate": _invoice_error_rate,
}


def _contract_digest(workspace: Workspace) -> str:
    """Digest of everything that defines what a run computes.

    Covers the schema version, the semantics id and version, the full
    evaluation contract, the interval events' domain declarations, the
    date-only timezone policy, and the guardrail evidence conventions —
    so a definition or contract edit changes the digest even when the
    report happens to stay the same.
    """
    interval = workspace.evaluation.interval
    raw = json.dumps(
        {
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "semantics": {
                "id": "interval-cohorts",
                "version": _SEMANTIC_VERSION,
            },
            "evaluation": dataclasses.asdict(workspace.evaluation),
            "events": {
                name: dataclasses.asdict(workspace.domain.events[name])
                for name in (interval.start_event, interval.end_event)
                if name in workspace.domain.events
            },
            "date_only_timezone": (
                workspace.domain.policies.event_times.date_only_timezone
            ),
            "guardrail_evidence": {
                "invoice_error_rate": {
                    "entity": _INVOICE_ENTITY,
                    "job_ref": _INVOICE_JOB_REF,
                    "error_flag": _INVOICE_ERROR_FLAG,
                }
            },
        },
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "EvaluationResult",
    "get_evaluation_run",
    "run_evaluation",
]
