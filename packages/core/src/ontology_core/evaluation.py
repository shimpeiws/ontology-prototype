"""Validation of the evaluation contract (evaluation.yaml).

Outcome evaluation over canonical state — pairing interval events into
baseline/after cohorts and freezing reports — lives in ``outcome.py``.
"""

from __future__ import annotations

from typing import Any

from .conditions import condition_fields, validate_condition
from .diagnostics import Diagnostics
from .domain import suggest
from .model import (
    DURATION_UNITS,
    ORDER_COMPARATORS,
    DomainDefinition,
    EvaluationDefinition,
    GuardrailSpec,
    IntervalSpec,
    MetricSpec,
    TargetSpec,
)
from .schema import check_name, err_at, get_map, get_num, get_str, unknown_keys

AGGREGATIONS = ("median", "mean", "min", "max", "count")
UNITS = DURATION_UNITS
METRICS = ("duration",)
COMPARATORS = ORDER_COMPARATORS
GUARDRAIL_COMPARATORS = ("not_increase",) + COMPARATORS


def validate_evaluation(
    doc: dict, diag: Diagnostics, file: str, domain: DomainDefinition | None
) -> EvaluationDefinition | None:
    err = err_at(diag, file)
    start = len(diag.items)
    unknown_keys(doc, {"schema_version", "kind", "evaluation"}, err, "")

    section = get_map(doc, "evaluation", err, "")
    if section is None:
        return None
    unknown_keys(
        section,
        {
            "name",
            "description",
            "interval",
            "metric",
            "aggregation",
            "unit",
            "baseline",
            "target",
            "guardrails",
            "metrics",
        },
        err,
        "evaluation",
    )

    name = get_str(section, "name", err, "evaluation")
    description = section.get("description")
    if description is not None and not isinstance(description, str):
        err("SCHEMA.INVALID_TYPE", "evaluation.description", "'description' must be a string")
        description = None

    interval = _validate_interval(section.get("interval"), err, domain)
    metric = get_str(section, "metric", err, "evaluation")
    if metric is not None and metric not in METRICS:
        err(
            "EVAL.INVALID_METRIC",
            "evaluation.metric",
            f"unsupported metric {metric!r}",
            hint=f"supported metrics: {', '.join(METRICS)}",
        )
        metric = None
    aggregation = get_str(section, "aggregation", err, "evaluation")
    if aggregation is not None and aggregation not in AGGREGATIONS:
        err(
            "EVAL.INVALID_AGGREGATION",
            "evaluation.aggregation",
            f"unknown aggregation {aggregation!r}",
            hint=f"allowed: {', '.join(AGGREGATIONS)}",
        )
        aggregation = None
    unit = get_str(section, "unit", err, "evaluation")
    if unit is not None and unit not in UNITS:
        err(
            "EVAL.INVALID_UNIT",
            "evaluation.unit",
            f"unknown unit {unit!r}",
            hint=f"allowed: {', '.join(UNITS)}",
        )
        unit = None

    baseline = _validate_baseline(section.get("baseline"), err)
    target = _validate_target(section.get("target"), err)
    guardrails = _validate_guardrails(section.get("guardrails"), err)
    metrics = _validate_metrics(section.get("metrics"), err, domain)

    own_errors = any(d.severity == "error" for d in diag.items[start:])
    if (
        own_errors
        or name is None
        or interval is None
        or metric is None
        or aggregation is None
        or unit is None
        or baseline is None
        or target is None
    ):
        return None
    return EvaluationDefinition(
        name=name,
        description=description,
        interval=interval,
        metric=metric,
        aggregation=aggregation,
        unit=unit,
        baseline=baseline,
        target=target,
        guardrails=guardrails,
        metrics=metrics,
    )


def _validate_interval(
    node: Any, err, domain: DomainDefinition | None
) -> IntervalSpec | None:
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", "evaluation.interval", "'interval' must be a mapping")
        return None
    unknown_keys(node, {"start_event", "end_event"}, err, "evaluation.interval")
    start_event = get_str(node, "start_event", err, "evaluation.interval")
    end_event = get_str(node, "end_event", err, "evaluation.interval")

    start_type = end_type = None
    if domain is not None:
        if start_event is not None:
            start_type = domain.events.get(start_event)
            if start_type is None:
                err(
                    "EVAL.UNKNOWN_EVENT",
                    "evaluation.interval.start_event",
                    f"unknown event type {start_event!r}",
                    hint=suggest(start_event, list(domain.events)),
                )
        if end_event is not None:
            end_type = domain.events.get(end_event)
            if end_type is None:
                err(
                    "EVAL.UNKNOWN_EVENT",
                    "evaluation.interval.end_event",
                    f"unknown event type {end_event!r}",
                    hint=suggest(end_event, list(domain.events)),
                )

    if (
        start_event is not None
        and end_event is not None
        and start_event == end_event
    ):
        err(
            "EVAL.SAME_EVENT",
            "evaluation.interval",
            "start_event and end_event must be different event types",
        )
        return None
    if (
        start_type is not None
        and end_type is not None
        and start_type.subject != end_type.subject
    ):
        err(
            "EVAL.SUBJECT_MISMATCH",
            "evaluation.interval",
            f"interval spans different subjects: {start_type.subject!r} and "
            f"{end_type.subject!r}; an interval is measured on one entity",
        )
        return None
    if start_event is None or end_event is None:
        return None
    return IntervalSpec(start_event=start_event, end_event=end_event)


def _validate_baseline(node: Any, err) -> float | None:
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", "evaluation.baseline", "'baseline' must be a mapping")
        return None
    unknown_keys(node, {"value", "description"}, err, "evaluation.baseline")
    value = get_num(node, "value", err, "evaluation.baseline")
    if value is not None and value < 0:
        err(
            "EVAL.INVALID_VALUE",
            "evaluation.baseline.value",
            "baseline value must be non-negative",
        )
        return None
    return value


def _validate_target(node: Any, err) -> TargetSpec | None:
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", "evaluation.target", "'target' must be a mapping")
        return None
    unknown_keys(node, {"comparator", "value", "description"}, err, "evaluation.target")
    comparator = get_str(node, "comparator", err, "evaluation.target")
    if comparator is not None and comparator not in COMPARATORS:
        err(
            "EVAL.INVALID_COMPARATOR",
            "evaluation.target.comparator",
            f"unknown comparator {comparator!r}",
            hint=f"allowed: {', '.join(COMPARATORS)} ('lt' is strict)",
        )
        comparator = None
    value = get_num(node, "value", err, "evaluation.target")
    if comparator is None or value is None:
        return None
    return TargetSpec(comparator=comparator, value=value)


def _validate_metrics(
    node: Any, err, domain: DomainDefinition | None
) -> dict[str, MetricSpec]:
    """Validate the ``evaluation.metrics`` map of declared guardrail metrics.

    Each metric declares the entity it counts, the ref property linking
    that entity to a cohort subject, and one predicate — a ``flag``
    boolean property or a ``where`` condition. When the domain loaded,
    the declarations are checked against it so a typo fails at check
    time instead of surfacing as an unavailable guardrail at evaluate
    time.
    """
    metrics: dict[str, MetricSpec] = {}
    if node is None:
        return metrics
    if not isinstance(node, dict):
        err(
            "SCHEMA.INVALID_TYPE",
            "evaluation.metrics",
            "'metrics' must be a mapping of metric names to declarations",
        )
        return metrics
    for name, item in node.items():
        path = f"evaluation.metrics.{name}"
        if check_name(name, err, path, "metric") is None:
            continue
        spec = _validate_metric(name, item, err, path, domain)
        if spec is not None:
            metrics[name] = spec
    return metrics


def _validate_metric(
    name: str, node: Any, err, path: str, domain: DomainDefinition | None
) -> MetricSpec | None:
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", path, "metric must be a mapping")
        return None
    unknown_keys(
        node,
        {"entity", "ref", "flag", "where", "noun", "description"},
        err,
        path,
    )
    entity = get_str(node, "entity", err, path)
    ref = get_str(node, "ref", err, path)

    flag = get_str(node, "flag", err, path, required=False)
    where = None
    if "where" in node:
        where = validate_condition(node["where"], err, f"{path}.where")
    if ("flag" in node) == ("where" in node):
        err(
            "EVAL.INVALID_PREDICATE",
            path,
            "metric requires exactly one predicate: 'flag' (a boolean "
            "property) or 'where' (a condition)",
        )

    noun = get_str(node, "noun", err, path, required=False)
    description = node.get("description")
    if description is not None and not isinstance(description, str):
        err(
            "SCHEMA.INVALID_TYPE",
            f"{path}.description",
            "'description' must be a string",
        )
        description = None

    entity_spec = None
    if domain is not None and entity is not None:
        entity_spec = domain.entities.get(entity)
        if entity_spec is None:
            err(
                "EVAL.UNKNOWN_ENTITY",
                f"{path}.entity",
                f"unknown entity {entity!r}",
                hint=suggest(entity, list(domain.entities)),
            )
    if entity_spec is not None and ref is not None:
        ref_spec = entity_spec.properties.get(ref)
        if ref_spec is None:
            err(
                "EVAL.UNKNOWN_FIELD",
                f"{path}.ref",
                f"{ref!r} is not a declared property of {entity!r}",
                hint=suggest(ref, list(entity_spec.properties)),
            )
        elif ref_spec.type != "ref":
            err(
                "EVAL.INVALID_METRIC_REF",
                f"{path}.ref",
                f"{entity}.{ref} is a {ref_spec.type} property; 'ref' "
                "must name a ref-typed property",
            )
    if entity_spec is not None and flag is not None:
        flag_spec = entity_spec.properties.get(flag)
        if flag_spec is None:
            err(
                "EVAL.UNKNOWN_FIELD",
                f"{path}.flag",
                f"{flag!r} is not a declared property of {entity!r}",
                hint=suggest(flag, list(entity_spec.properties)),
            )
        elif flag_spec.type != "boolean":
            err(
                "EVAL.INVALID_METRIC_FLAG",
                f"{path}.flag",
                f"{entity}.{flag} is a {flag_spec.type} property; 'flag' "
                "must name a boolean property",
            )
    if entity_spec is not None and where is not None:
        for field_name in condition_fields(where.raw):
            if field_name not in entity_spec.properties:
                err(
                    "EVAL.UNKNOWN_FIELD",
                    f"{path}.where",
                    f"condition field {field_name!r} is not a declared "
                    f"property of {entity!r}",
                    hint=suggest(field_name, list(entity_spec.properties)),
                )

    if (
        entity is None
        or ref is None
        or ("flag" in node) == ("where" in node)
        or (flag is None and where is None)
    ):
        return None
    spec = MetricSpec(
        name=name,
        entity=entity,
        ref=ref,
        flag=flag,
        where=where,
        noun=noun,
        description=description,
    )
    keys = spec.evidence_keys()
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        err(
            "EVAL.METRIC_NOUN_COLLISION",
            path,
            f"evidence key(s) {', '.join(repr(k) for k in duplicates)} "
            "would be emitted more than once; set 'noun' or rename the "
            "entity so the nouns differ from each other and from the "
            "reserved keys 'rate', 'postdating_as_of', 'entity_ids'",
        )
        return None
    return spec


def _validate_guardrails(node: Any, err) -> list[GuardrailSpec]:
    guardrails: list[GuardrailSpec] = []
    if node is None:
        return guardrails
    if not isinstance(node, list):
        err("SCHEMA.INVALID_TYPE", "evaluation.guardrails", "'guardrails' must be a list")
        return guardrails
    seen: set[str] = set()
    for index, item in enumerate(node):
        path = f"evaluation.guardrails[{index}]"
        if not isinstance(item, dict):
            err("SCHEMA.INVALID_TYPE", path, "guardrail must be a mapping")
            continue
        unknown_keys(item, {"name", "metric", "comparator", "value", "description"}, err, path)
        gname = get_str(item, "name", err, path)
        if gname is not None:
            if gname in seen:
                err("EVAL.DUPLICATE_GUARDRAIL", f"{path}.name", f"duplicate guardrail {gname!r}")
                gname = None
            else:
                seen.add(gname)
        metric = get_str(item, "metric", err, path)
        comparator = get_str(item, "comparator", err, path)
        if comparator is not None and comparator not in GUARDRAIL_COMPARATORS:
            err(
                "EVAL.INVALID_COMPARATOR",
                f"{path}.comparator",
                f"unknown guardrail comparator {comparator!r}",
                hint=f"allowed: {', '.join(GUARDRAIL_COMPARATORS)}",
            )
            comparator = None
        value = None
        if comparator == "not_increase":
            if "value" in item:
                err(
                    "EVAL.INVALID_GUARDRAIL",
                    f"{path}.value",
                    "'not_increase' compares against the baseline and takes no 'value'",
                )
        elif comparator is not None:
            value = get_num(item, "value", err, path)
        gdescription = item.get("description")
        if gdescription is not None and not isinstance(gdescription, str):
            err("SCHEMA.INVALID_TYPE", f"{path}.description", "'description' must be a string")
            gdescription = None
        if gname is not None and metric is not None and comparator is not None:
            guardrails.append(
                GuardrailSpec(
                    name=gname,
                    metric=metric,
                    comparator=comparator,
                    value=value,
                    description=gdescription,
                )
            )
    return guardrails
