"""Outcome evaluation: baseline/after cohorts over canonical intervals.

The workspace evaluation contract (``evaluation.yaml``, validated in
evaluation.py) declares an interval — a start event and an end event on
one subject — plus the aggregation, a declared baseline, a strict target,
guardrails, and the metrics guardrails measure (numerator/denominator
over entities related to cohort subjects via a declared ref, gated by a
boolean ``flag`` or a ``where`` condition). ``run_evaluation`` pairs
those interval events per canonical subject at an explicit ``as_of``,
splits subjects into a baseline and an after cohort at an explicit
``boundary``, and reports aggregates, pending and invalid intervals, the
target verdict, and guardrail verdicts computed from explicit evidence
counts — then freezes the full report into the append-only
``evaluation_runs`` table.

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
- ``excluded`` — no visible events at all: a future-dated start event
  (``EVALUATION.FUTURE_EVENT``) or a subject whose only interval facts
  are all after ``as_of``. Excluded subjects are inspectable in the
  report inputs with their future fact keys; an all-future subject
  emits no diagnostic because it asserts nothing about the as-of state.

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
from .conditions import condition_fields, evaluate_condition
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR
from .model import GuardrailSpec, MetricSpec, Workspace
from .trace import JsonlTraceSink, TraceSink
from .workspace import SUPPORTED_SCHEMA_VERSION

# Version of the cohort/pairing and evidence semantics. Bump when the
# rules — including where guardrail semantics come from — change so
# previously frozen reports carry a different contract digest.
_SEMANTIC_VERSION = 3

# Seconds in one declared contract unit; durations are reported in the
# contract's unit rather than raw seconds.
_UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}

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
    workspace: Workspace | str | Path,
    run_id: str,
    *,
    store: CanonicalStore | None = None,
) -> dict[str, Any] | None:
    """Read back a frozen evaluation report by run id.

    Accepts a workspace path as well as a ``Workspace``: inspecting a
    frozen report needs only the canonical store's location, not the
    current definitions — a report stays inspectable even when a later
    config edit makes the workspace invalid.
    """
    if store is None:
        root = workspace.path if isinstance(workspace, Workspace) else workspace
        store = SqliteCanonicalStore(Path(root) / RUNTIME_DIR)
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
        if evaluation.aggregation == "count":
            # A count delta is interval-count movement, not a duration
            # verdict — "fewer measured intervals" is not an improvement.
            direction = (
                "decrease"
                if delta > 0
                else "increase"
                if delta < 0
                else "unchanged"
            )
        else:
            direction = (
                "improvement"
                if delta > 0
                else "regression"
                if delta < 0
                else "unchanged"
            )
        change: dict[str, Any] = {
            "status": "measured",
            "value": delta,
            "direction": direction,
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
        _evaluate_guardrail(
            workspace, store, spec, cohort_subjects, subject_entity, as_of, diag
        )
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
    if not durations:
        # No measured intervals means no aggregate — for every
        # aggregation, count included. The interval count itself is
        # already reported explicitly in the cohort's counts and sample;
        # returning 0 here would feed a fabricated aggregate into
        # change and target.
        return None
    if aggregation == "count":
        return float(len(durations))
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
    subject_entity: str,
    as_of: datetime,
    diag: Diagnostics,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "name": spec.name,
        "metric": spec.metric,
        "comparator": spec.comparator,
    }
    metric = workspace.evaluation.metrics.get(spec.metric)
    if metric is None:
        report["status"] = "unavailable"
        report["reason"] = (
            f"metric {spec.metric!r} is not declared in "
            "evaluation.metrics"
        )
        diag.warning(
            "EVALUATION.UNSUPPORTED_GUARDRAIL",
            f"guardrail {spec.name!r} uses metric {spec.metric!r}, which "
            "is not declared in evaluation.metrics",
        )
        return report

    # The evidence label is the metric's own name minus a trailing
    # "_rate" — an "error_rate" metric reports "no error evidence".
    label = spec.metric.removesuffix("_rate").replace("_", "-")
    evidence = _metric_evidence(
        workspace, store, metric, cohort_subjects, subject_entity, as_of
    )
    report["evidence"] = evidence
    baseline_rate = evidence["baseline"]["rate"]
    after_rate = evidence["after"]["rate"]

    if evidence.get("applicable") is False:
        report["status"] = "unavailable"
        report["reason"] = evidence["reason"]
    elif spec.comparator == "not_increase":
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
                f"no {label} evidence for the "
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
                f"no {label} evidence for the after cohort"
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


def _metric_evidence(
    workspace: Workspace,
    store: CanonicalStore,
    metric: MetricSpec,
    cohort_subjects: dict[str, set[str]],
    subject_entity: str,
    as_of: datetime,
) -> dict[str, Any]:
    """Explicit numerator/denominator counts for a declared metric.

    The denominator of a cohort is the number of ``metric.entity``
    entities linked (through the ``metric.ref`` ref) to a subject of
    that cohort; for a ``flag`` predicate only entities carrying a
    boolean flag value count — an entity whose flag is absent or not
    boolean is an evidence gap — while a ``where`` predicate is total:
    every linked entity is scored and condition-matching entities count
    toward the numerator. Entities linked to no cohort subject, and
    entities whose last state-changing observation postdates ``as_of``
    — stored entity state cannot prove what its properties were at the
    instant — are reported as evidence gaps, never silently folded into
    the rate.

    The ``ref`` is a typed reference, so the metric applies only to
    intervals whose subject is the ref's declared target; for any other
    subject entity the declaration is not applicable and the guardrail
    reports unavailable.
    """
    entity_spec = workspace.domain.entities.get(metric.entity)
    ref_spec = (
        entity_spec.properties.get(metric.ref) if entity_spec else None
    )
    ref_target = ref_spec.ref if ref_spec is not None else None

    nouns = metric.evidence_nouns()
    convention: dict[str, Any] = {
        "entity": metric.entity,
        f"{metric.ref}_ref": metric.ref,
    }
    if metric.flag is not None:
        convention[f"{metric.flag}_flag"] = metric.flag
    elif metric.where is not None:
        convention["where"] = metric.where.raw
    convention["subject_entity"] = ref_target

    if ref_target is None or ref_target != subject_entity:
        # Without the typed gate, an unrelated entity's ref could
        # collide with a same-id cohort subject of another type. An
        # unresolved declaration — possible only for a directly
        # constructed Workspace, since load_workspace validates the
        # metric against the domain — is not applicable either.
        if ref_target is None:
            reason = (
                f"{metric.entity}.{metric.ref} is not a declared "
                "reference in the domain"
            )
        else:
            reason = (
                f"{metric.entity}.{metric.ref} is a {ref_target} "
                "reference; the interval subject is "
                f"{subject_entity}, so this guardrail does not apply"
            )
        return {
            "convention": convention,
            "applicable": False,
            "reason": reason,
            "baseline": _empty_evidence_bucket(metric),
            "after": _empty_evidence_bucket(metric),
        }

    buckets: dict[str, dict[str, Any]] = {
        name: {
            "scored": 0,
            "counted": 0,
            "missing": 0,
            "postdating_as_of": 0,
            "entity_ids": [],
        }
        for name in ("baseline", "after")
    }
    unmatched = 0
    for entity in store.entities_of_type(metric.entity):
        properties = entity["properties"]
        linked = properties.get(metric.ref)
        cohort = next(
            (
                name
                for name in ("baseline", "after")
                if isinstance(linked, str) and linked in cohort_subjects[name]
            ),
            None,
        )
        if cohort is None:
            unmatched += 1
            continue
        bucket = buckets[cohort]
        changed_at = _parse_iso(entity.get("properties_changed_at"))
        if changed_at is None or changed_at > as_of:
            # The stored properties last *changed* after the evaluation
            # instant — a later correction must not rewrite what the
            # guardrail saw at as_of. Re-observing identical properties
            # does not move this timestamp, so repeat ingestion cannot
            # erase certifiable evidence. Excluded from the denominator
            # and counted explicitly.
            bucket["postdating_as_of"] += 1
            continue
        bucket["entity_ids"].append(entity["entity_id"])
        if metric.flag is not None:
            flag_value = properties.get(metric.flag)
            if isinstance(flag_value, bool):
                bucket["scored"] += 1
                if flag_value:
                    bucket["counted"] += 1
            else:
                bucket["missing"] += 1
        else:
            bucket["scored"] += 1
            if evaluate_condition(metric.where, properties):
                bucket["counted"] += 1

    evidence: dict[str, Any] = {
        "convention": convention,
        f"unmatched_{nouns[0]}": unmatched,
    }
    for name in ("baseline", "after"):
        evidence[name] = _evidence_bucket(metric, buckets[name])
    return evidence


def _empty_evidence_bucket(metric: MetricSpec) -> dict[str, Any]:
    return _evidence_bucket(
        metric,
        {
            "scored": 0,
            "counted": 0,
            "missing": 0,
            "postdating_as_of": 0,
            "entity_ids": [],
        },
    )


def _evidence_bucket(
    metric: MetricSpec, bucket: dict[str, Any]
) -> dict[str, Any]:
    values: list[Any] = [
        bucket["scored"],
        bucket["counted"],
        (
            bucket["counted"] / bucket["scored"]
            if bucket["scored"]
            else None
        ),
    ]
    if metric.flag is not None:
        values.append(bucket["missing"])
    values += [bucket["postdating_as_of"], sorted(bucket["entity_ids"])]
    return dict(zip(metric.evidence_keys(), values))


def _contract_digest(workspace: Workspace) -> str:
    """Digest of everything that defines what a run computes.

    Covers the schema version, the semantics id and version, the full
    evaluation contract — including the declared guardrail metric
    semantics — the domain property specs each metric's verdict depends
    on (the ref's target gates applicability), the interval events'
    domain declarations, and the date-only timezone policy, so a
    definition or contract edit changes the digest even when the report
    happens to stay the same.
    """
    interval = workspace.evaluation.interval
    metric_fields: dict[str, Any] = {}
    for name, metric in workspace.evaluation.metrics.items():
        fields = [metric.ref]
        if metric.flag is not None:
            fields.append(metric.flag)
        if metric.where is not None:
            fields += condition_fields(metric.where.raw)
        entity_spec = workspace.domain.entities.get(metric.entity)
        metric_fields[name] = {
            "entity": metric.entity,
            "properties": {
                field: dataclasses.asdict(entity_spec.properties[field])
                for field in fields
                if entity_spec is not None
                and field in entity_spec.properties
            },
        }
    raw = json.dumps(
        {
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "semantics": {
                "id": "interval-cohorts",
                "version": _SEMANTIC_VERSION,
            },
            "evaluation": dataclasses.asdict(workspace.evaluation),
            "metric_fields": metric_fields,
            "events": {
                name: dataclasses.asdict(workspace.domain.events[name])
                for name in (interval.start_event, interval.end_event)
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


def _parse_iso(value: Any) -> datetime | None:
    """Best-effort parse of a stored timestamp into an aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "EvaluationResult",
    "get_evaluation_run",
    "run_evaluation",
]
