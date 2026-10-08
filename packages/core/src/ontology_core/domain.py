"""Validation of the domain definition (domain.yaml)."""

from __future__ import annotations

import difflib
import math
from typing import Any

from .conditions import condition_fields, validate_condition
from .diagnostics import Diagnostics
from .model import (
    DURATION_UNITS,
    ORDER_COMPARATORS,
    PROPERTY_TYPES,
    DomainDefinition,
    DomainPolicies,
    ElapsedSpec,
    EntityType,
    EventTimePolicy,
    EventType,
    ProjectionSpec,
    PropertySpec,
    SelectionSpec,
)
from .schema import check_name, err_at, get_map, get_num, get_str, unknown_keys, warn_at

SUPPORTED_TIMEZONES = ("UTC",)

# Schema v1 fixes the canonical event time field names. The policy declares
# them explicitly so every file agrees on the same temporal contract.
OCCURRENCE_FIELD = "occurred_at"
OBSERVATION_FIELD = "observed_at"


def suggest(name: str, candidates: list[str] | tuple[str, ...] | Any) -> str | None:
    matches = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.6)
    return f"did you mean {matches[0]!r}?" if matches else None


def validate_property_spec(
    name: str,
    node: Any,
    err,
    path: str,
    pending_refs: list[tuple[str, str]],
) -> PropertySpec | None:
    """Validate one typed property spec. Ref targets are recorded in
    ``pending_refs`` as ``(path, entity_name)`` for deferred resolution."""
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", path, "property spec must be a mapping")
        return None
    unknown_keys(node, {"type", "required", "ref", "description"}, err, path)

    ptype = node.get("type")
    ok = True
    if not isinstance(ptype, str) or not ptype:
        err("SCHEMA.MISSING_FIELD", f"{path}.type", "property requires a 'type' string")
        ok = False
    elif ptype not in PROPERTY_TYPES:
        err(
            "SCHEMA.INVALID_PROPERTY_TYPE",
            f"{path}.type",
            f"unknown property type {ptype!r}",
            hint=f"allowed types: {', '.join(PROPERTY_TYPES)}",
        )
        ok = False

    ref = node.get("ref")
    if ptype == "ref":
        if not isinstance(ref, str) or not ref:
            err(
                "DOMAIN.REF_TARGET_MISSING",
                f"{path}.ref",
                "property of type 'ref' requires a 'ref' entity name",
            )
            ok = False
        else:
            pending_refs.append((f"{path}.ref", ref))
    elif ref is not None:
        err(
            "DOMAIN.UNEXPECTED_REF",
            f"{path}.ref",
            f"'ref' is only allowed when type is 'ref', got type {ptype!r}",
        )
        ok = False

    required = node.get("required", False)
    if not isinstance(required, bool):
        err("SCHEMA.INVALID_TYPE", f"{path}.required", "'required' must be a boolean")
        ok = False

    description = node.get("description")
    if description is not None and not isinstance(description, str):
        err("SCHEMA.INVALID_TYPE", f"{path}.description", "'description' must be a string")
        ok = False

    if not ok:
        return None
    return PropertySpec(
        name=name,
        type=ptype,
        required=required,
        ref=ref,
        description=description,
    )


def _validate_properties(
    node: Any,
    err,
    path: str,
    pending_refs: list[tuple[str, str]],
) -> dict[str, PropertySpec]:
    properties: dict[str, PropertySpec] = {}
    if node is None:
        return properties
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", path, "'properties' must be a mapping")
        return properties
    for prop_name, spec in node.items():
        prop_path = f"{path}.{prop_name}"
        if check_name(prop_name, err, prop_path, "property") is None:
            continue
        result = validate_property_spec(prop_name, spec, err, prop_path, pending_refs)
        if result is not None:
            properties[prop_name] = result
    return properties


def _validate_policies(node: Any, err, path: str) -> DomainPolicies | None:
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", path, "'policies' must be a mapping")
        return None
    unknown_keys(node, {"entity_id_type", "event_times"}, err, path)

    ok = True
    entity_id_type = get_str(node, "entity_id_type", err, path)
    if entity_id_type is not None and entity_id_type != "string":
        err(
            "DOMAIN.INVALID_ID_POLICY",
            f"{path}.entity_id_type",
            f"entity_id_type must be 'string', got {entity_id_type!r}",
            hint="entity identifiers are opaque strings and are never coerced",
        )
        ok = False

    times = get_map(node, "event_times", err, path)
    occurrence = observation = timezone = None
    if times is not None:
        unknown_keys(
            times,
            {"occurrence_field", "observation_field", "date_only_timezone"},
            err,
            f"{path}.event_times",
        )
        occurrence = get_str(times, "occurrence_field", err, f"{path}.event_times")
        observation = get_str(times, "observation_field", err, f"{path}.event_times")
        timezone = get_str(times, "date_only_timezone", err, f"{path}.event_times")
        if occurrence is not None and occurrence != OCCURRENCE_FIELD:
            err(
                "DOMAIN.INVALID_TIME_POLICY",
                f"{path}.event_times.occurrence_field",
                f"schema v1 fixes the occurrence field name to {OCCURRENCE_FIELD!r}, "
                f"got {occurrence!r}",
            )
            ok = False
        if observation is not None and observation != OBSERVATION_FIELD:
            err(
                "DOMAIN.INVALID_TIME_POLICY",
                f"{path}.event_times.observation_field",
                f"schema v1 fixes the observation field name to {OBSERVATION_FIELD!r}, "
                f"got {observation!r}",
            )
            ok = False
        if timezone is not None and timezone not in SUPPORTED_TIMEZONES:
            err(
                "DOMAIN.UNSUPPORTED_TIMEZONE",
                f"{path}.event_times.date_only_timezone",
                f"unsupported date_only_timezone {timezone!r}",
                hint=(
                    "date-only values are interpreted as midnight in this zone; "
                    f"supported: {', '.join(SUPPORTED_TIMEZONES)}"
                ),
            )
            ok = False
    else:
        ok = False

    if not ok or entity_id_type is None or occurrence is None or observation is None or timezone is None:
        return None
    return DomainPolicies(
        entity_id_type=entity_id_type,
        event_times=EventTimePolicy(
            occurrence_field=occurrence,
            observation_field=observation,
            date_only_timezone=timezone,
        ),
    )


def validate_domain(doc: dict, diag: Diagnostics, file: str) -> DomainDefinition | None:
    err = err_at(diag, file)
    warn = warn_at(diag, file)
    start = len(diag.items)
    unknown_keys(doc, {"schema_version", "kind", "domain"}, err, "")

    section = get_map(doc, "domain", err, "")
    if section is None:
        return None
    unknown_keys(
        section,
        {"name", "description", "policies", "entities", "events", "projections"},
        err,
        "domain",
    )

    name = get_str(section, "name", err, "domain")
    description = section.get("description")
    if description is not None and not isinstance(description, str):
        err("SCHEMA.INVALID_TYPE", "domain.description", "'description' must be a string")
        description = None

    policies = _validate_policies(section.get("policies"), err, "domain.policies")
    if "policies" not in section:
        err("SCHEMA.MISSING_FIELD", "domain.policies", "missing required field 'policies'")

    pending_refs: list[tuple[str, str]] = []
    entity_specs: list[tuple[str, str, Any]] = []  # (path, name, spec node)
    entities: dict[str, EntityType] = {}

    entities_node = section.get("entities")
    if entities_node is None:
        err("SCHEMA.MISSING_FIELD", "domain.entities", "missing required field 'entities'")
    elif not isinstance(entities_node, dict):
        err("SCHEMA.INVALID_TYPE", "domain.entities", "'entities' must be a mapping")
    else:
        for entity_name, spec in entities_node.items():
            entity_path = f"domain.entities.{entity_name}"
            if check_name(entity_name, err, entity_path, "entity") is None:
                continue
            entity_specs.append((entity_path, entity_name, spec))

    # Entity names are gathered before validation so references resolve in any order.
    entity_names = [name for _, name, _ in entity_specs]
    for entity_path, entity_name, spec in entity_specs:
        if not isinstance(spec, dict):
            err("SCHEMA.INVALID_TYPE", entity_path, "entity spec must be a mapping")
            continue
        unknown_keys(spec, {"description", "properties"}, err, entity_path)
        entity_description = spec.get("description")
        if entity_description is not None and not isinstance(entity_description, str):
            err("SCHEMA.INVALID_TYPE", f"{entity_path}.description", "'description' must be a string")
            entity_description = None
        properties = _validate_properties(
            spec.get("properties"), err, f"{entity_path}.properties", pending_refs
        )
        entities[entity_name] = EntityType(
            name=entity_name, properties=properties, description=entity_description
        )

    events: dict[str, EventType] = {}
    events_node = section.get("events")
    if events_node is None:
        err("SCHEMA.MISSING_FIELD", "domain.events", "missing required field 'events'")
    elif not isinstance(events_node, dict):
        err("SCHEMA.INVALID_TYPE", "domain.events", "'events' must be a mapping")
    elif not events_node:
        warn("DOMAIN.EMPTY_EVENTS", "domain.events", "no event types declared")
    else:
        for event_name, spec in events_node.items():
            event_path = f"domain.events.{event_name}"
            if check_name(event_name, err, event_path, "event") is None:
                continue
            if not isinstance(spec, dict):
                err("SCHEMA.INVALID_TYPE", event_path, "event spec must be a mapping")
                continue
            unknown_keys(spec, {"description", "subject", "properties"}, err, event_path)
            subject = spec.get("subject")
            subject_ok = isinstance(subject, str) and bool(subject)
            if not subject_ok:
                err(
                    "SCHEMA.MISSING_FIELD",
                    f"{event_path}.subject",
                    "event requires a 'subject' entity name",
                )
            event_description = spec.get("description")
            if event_description is not None and not isinstance(event_description, str):
                err("SCHEMA.INVALID_TYPE", f"{event_path}.description", "'description' must be a string")
                event_description = None
            properties = _validate_properties(
                spec.get("properties"), err, f"{event_path}.properties", pending_refs
            )
            if subject_ok:
                events[event_name] = EventType(
                    name=event_name,
                    subject=subject,
                    properties=properties,
                    description=event_description,
                )

    projections: dict[str, ProjectionSpec] = {}
    # Projection subjects are checked against entities even when the rest of
    # the spec is invalid — the deferred check must not depend on selection
    # validity.
    projection_subjects: list[tuple[str, str]] = []  # (path, subject)
    projections_node = section.get("projections")
    if projections_node is not None:
        if not isinstance(projections_node, dict):
            err("SCHEMA.INVALID_TYPE", "domain.projections", "'projections' must be a mapping")
        else:
            for proj_name, spec in projections_node.items():
                proj_path = f"domain.projections.{proj_name}"
                if check_name(proj_name, err, proj_path, "projection") is None:
                    continue
                if not isinstance(spec, dict):
                    err("SCHEMA.INVALID_TYPE", proj_path, "projection spec must be a mapping")
                    continue
                unknown_keys(spec, {"description", "subject", "select"}, err, proj_path)
                subject = spec.get("subject")
                subject_ok = isinstance(subject, str) and bool(subject)
                if not subject_ok:
                    err(
                        "SCHEMA.MISSING_FIELD",
                        f"{proj_path}.subject",
                        "projection requires a 'subject' entity name",
                    )
                else:
                    projection_subjects.append((f"{proj_path}.subject", subject))
                proj_description = spec.get("description")
                if proj_description is not None and not isinstance(proj_description, str):
                    err("SCHEMA.INVALID_TYPE", f"{proj_path}.description", "'description' must be a string")
                    proj_description = None
                selection = _validate_select(
                    spec.get("select"),
                    err,
                    f"{proj_path}.select",
                    events=events,
                    entities=entities,
                    subject=subject if subject_ok else None,
                )
                if subject_ok and selection is not None:
                    projections[proj_name] = ProjectionSpec(
                        name=proj_name,
                        subject=subject,
                        select=selection,
                        description=proj_description,
                    )

    # Deferred reference resolution: property refs and event/projection subjects.
    for ref_path, target in pending_refs:
        if target not in entities:
            err(
                "DOMAIN.UNKNOWN_REF",
                ref_path,
                f"unknown entity type {target!r}",
                hint=suggest(target, entity_names),
            )
    for event_name, event in events.items():
        if event.subject not in entities:
            err(
                "DOMAIN.UNKNOWN_SUBJECT",
                f"domain.events.{event_name}.subject",
                f"event subject {event.subject!r} is not a declared entity",
                hint=suggest(event.subject, entity_names),
            )
    for subj_path, subj in projection_subjects:
        if subj not in entities:
            err(
                "DOMAIN.UNKNOWN_SUBJECT",
                subj_path,
                f"projection subject {subj!r} is not a declared entity",
                hint=suggest(subj, entity_names),
            )

    own_errors = any(d.severity == "error" for d in diag.items[start:])
    if own_errors or name is None or policies is None:
        return None
    return DomainDefinition(
        name=name,
        description=description,
        policies=policies,
        entities=entities,
        events=events,
        projections=projections,
    )


def _validate_select(
    node: Any,
    err,
    path: str,
    *,
    events: dict[str, EventType],
    entities: dict[str, EntityType],
    subject: str | None,
) -> SelectionSpec | None:
    """Validate a projection's declarative ``select`` block.

    The vocabulary is deliberately restricted: required/absent event lists,
    an optional as-of-relative ``elapsed`` comparison, and an optional
    ``where`` condition over the subject entity's declared properties.
    ``subject`` is None when the projection's own subject failed to parse —
    subject-dependent checks are skipped so errors don't cascade. An
    undeclared subject also skips them: DOMAIN.UNKNOWN_SUBJECT reports that
    on its own.
    """
    check_subject = subject if subject in entities else None
    if node is None:
        err(
            "SCHEMA.MISSING_FIELD",
            path,
            "projection requires a 'select' selection",
        )
        return None
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", path, "'select' must be a mapping")
        return None
    unknown_keys(
        node, {"required_events", "absent_events", "elapsed", "where"}, err, path
    )

    ok = True
    required = _validate_event_list(
        node, "required_events", err, path, events, check_subject, required=True
    )
    absent = _validate_event_list(
        node, "absent_events", err, path, events, check_subject, required=False
    )
    overlap = [name for name in required if name in absent]
    if overlap:
        err(
            "DOMAIN.OVERLAPPING_EVENTS",
            f"{path}.absent_events",
            f"event(s) cannot be both required and absent: {', '.join(overlap)}",
        )
        ok = False

    elapsed = None
    if "elapsed" in node:
        # An explicit `elapsed:` key must hold a mapping — a null value would
        # silently drop the projection below, so it is a schema error.
        elapsed = _validate_elapsed(node["elapsed"], err, f"{path}.elapsed", required)
        if elapsed is None:
            ok = False

    where = None
    if "where" in node:
        where = validate_condition(node["where"], err, f"{path}.where")
        if where is None:
            ok = False
        elif subject is not None and subject in entities:
            properties = entities[subject].properties
            for field_name in condition_fields(where.raw):
                if field_name not in properties:
                    err(
                        "DOMAIN.UNKNOWN_FIELD",
                        f"{path}.where",
                        f"condition field {field_name!r} is not a declared "
                        f"property of {subject!r}",
                        hint=suggest(field_name, list(properties)),
                    )
                    ok = False

    if not ok or not required:
        return None
    return SelectionSpec(
        required_events=tuple(required),
        absent_events=tuple(absent),
        elapsed=elapsed,
        where=where,
    )


def _validate_event_list(
    node: dict,
    key: str,
    err,
    path: str,
    events: dict[str, EventType],
    subject: str | None,
    *,
    required: bool,
) -> list[str]:
    """Validate a required_events/absent_events list: declared events bound
    to the projection's subject entity."""
    names: list[str] = []
    value = node.get(key)
    if value is None:
        if required:
            err(
                "SCHEMA.MISSING_FIELD",
                f"{path}.{key}",
                f"missing required field {key!r}",
            )
        return names
    if not isinstance(value, list):
        err(
            "SCHEMA.INVALID_TYPE",
            f"{path}.{key}",
            f"'{key}' must be a list of event names",
        )
        return names
    if required and not value:
        err(
            "SCHEMA.INVALID_VALUE",
            f"{path}.{key}",
            f"'{key}' must name at least one event",
        )
        return names
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}.{key}[{index}]"
        if not isinstance(item, str) or not item:
            err(
                "SCHEMA.INVALID_TYPE",
                item_path,
                "event name must be a non-empty string",
            )
            continue
        if item in seen:
            err(
                "DOMAIN.DUPLICATE_EVENT",
                item_path,
                f"duplicate event {item!r}",
            )
            continue
        seen.add(item)
        event = events.get(item)
        if event is None:
            err(
                "DOMAIN.UNKNOWN_EVENT",
                item_path,
                f"unknown event type {item!r}",
                hint=suggest(item, list(events)),
            )
            continue
        if subject is not None and event.subject != subject:
            err(
                "DOMAIN.EVENT_SUBJECT_MISMATCH",
                item_path,
                f"event {item!r} has subject {event.subject!r}; the "
                f"projection selects {subject!r}",
            )
            continue
        names.append(item)
    return names


def _validate_elapsed(
    node: Any, err, path: str, required_events: list[str]
) -> ElapsedSpec | None:
    """Validate the optional ``elapsed`` as-of-relative duration check."""
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", path, "'elapsed' must be a mapping")
        return None
    unknown_keys(node, {"event", "comparator", "value", "unit"}, err, path)

    ok = True
    event = node.get("event")
    if event is not None:
        if not isinstance(event, str) or not event:
            err(
                "SCHEMA.INVALID_TYPE",
                f"{path}.event",
                "'event' must be a non-empty string",
            )
            event = None
            ok = False
        elif required_events and event not in required_events:
            err(
                "DOMAIN.ELAPSED_EVENT",
                f"{path}.event",
                f"elapsed event {event!r} is not in required_events; the "
                "clock starts on a required event",
            )
            ok = False
    if event is None and required_events:
        event = required_events[0]

    comparator = get_str(node, "comparator", err, path)
    if comparator is not None and comparator not in ORDER_COMPARATORS:
        err(
            "DOMAIN.INVALID_COMPARATOR",
            f"{path}.comparator",
            f"unknown comparator {comparator!r}",
            hint=f"allowed: {', '.join(ORDER_COMPARATORS)} ('lt' is strict)",
        )
        ok = False

    value = get_num(node, "value", err, path)
    if value is not None and (not math.isfinite(value) or value < 0):
        err(
            "DOMAIN.INVALID_VALUE",
            f"{path}.value",
            "elapsed value must be a finite, non-negative number",
        )
        ok = False

    unit = get_str(node, "unit", err, path)
    if unit is not None and unit not in DURATION_UNITS:
        err(
            "DOMAIN.INVALID_UNIT",
            f"{path}.unit",
            f"unknown unit {unit!r}",
            hint=f"allowed: {', '.join(DURATION_UNITS)}",
        )
        ok = False

    if not ok or event is None or comparator is None or value is None or unit is None:
        return None
    return ElapsedSpec(
        event=event, comparator=comparator, value=value, unit=unit
    )
