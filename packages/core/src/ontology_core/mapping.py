"""Validation of the mapping definition (mapping.yaml)."""

from __future__ import annotations

from typing import Any

from .conditions import validate_condition
from .diagnostics import Diagnostics
from .domain import OBSERVATION_FIELD, OCCURRENCE_FIELD, suggest
from .model import (
    CAST_TYPES,
    DomainDefinition,
    EntityBinding,
    EventBinding,
    FieldAssignment,
    MappingDefinition,
    PropertySpec,
    SourceSpec,
    SubjectRef,
)
from .schema import err_at, get_list, get_map, get_str, unknown_keys, warn_at

SOURCE_FORMATS = ("csv", "json")
TIME_CASTS = ("date", "datetime")

_LITERAL_TYPES = {
    "string": str,
    "ref": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "date": str,
    "datetime": str,
}


def _validate_assignment(
    node: Any,
    err,
    path: str,
    target: PropertySpec | None,
    *,
    allowed_casts: tuple[str, ...] | None = CAST_TYPES,
    require_cast: bool = False,
) -> FieldAssignment | None:
    """Validate a ``{field|literal, as}`` assignment.

    ``target`` is the domain property the value maps to (None for ids and
    subjects). ``allowed_casts=None`` forbids ``as`` entirely (entity ids stay
    strings); ``require_cast=True`` makes ``as`` mandatory (event times).
    """
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", path, "assignment must be a mapping")
        return None
    unknown_keys(node, {"field", "literal", "as"}, err, path)

    ok = True
    has_field = "field" in node
    has_literal = "literal" in node
    if has_field == has_literal:
        err(
            "MAPPING.INVALID_ASSIGNMENT",
            path,
            "assignment requires exactly one of 'field' or 'literal'",
        )
        ok = False

    field_name = None
    literal = None
    if has_field:
        field_name = node["field"]
        if not isinstance(field_name, str) or not field_name:
            err(
                "MAPPING.INVALID_FIELD",
                f"{path}.field",
                "'field' must be a non-empty source column name",
            )
            ok = False
            field_name = None
    if has_literal:
        literal = node["literal"]

    cast = node.get("as")
    if cast is not None:
        if not isinstance(cast, str) or cast not in CAST_TYPES:
            err(
                "MAPPING.INVALID_CAST",
                f"{path}.as",
                f"'as' must be one of: {', '.join(CAST_TYPES)}",
            )
            ok = False
            cast = None
        elif allowed_casts is None:
            err(
                "MAPPING.INVALID_CAST",
                f"{path}.as",
                "'as' is not allowed here; entity ids are strings and are never cast",
            )
            ok = False
            cast = None
        elif cast not in allowed_casts:
            err(
                "MAPPING.INVALID_CAST",
                f"{path}.as",
                f"'as: {cast}' is not allowed here",
                hint=f"allowed casts: {', '.join(allowed_casts)}",
            )
            ok = False
            cast = None
    elif require_cast:
        err(
            "MAPPING.MISSING_CAST",
            f"{path}.as",
            "an explicit 'as' cast is required here",
            hint=f"allowed casts: {', '.join(allowed_casts or ())}",
        )
        ok = False

    if target is not None and ok:
        if target.type == "ref":
            if cast is not None:
                err(
                    "MAPPING.INCOMPATIBLE_CAST",
                    f"{path}.as",
                    f"ref property {target.name!r} takes a string id and cannot use 'as'",
                )
                ok = False
            if has_literal and not isinstance(literal, str):
                err(
                    "MAPPING.INCOMPATIBLE_LITERAL",
                    f"{path}.literal",
                    f"ref property {target.name!r} requires a string id literal",
                )
                ok = False
        else:
            if cast is None and target.type != "string":
                err(
                    "MAPPING.MISSING_CAST",
                    f"{path}.as",
                    f"property {target.name!r} of type {target.type!r} requires an explicit 'as' cast",
                )
                ok = False
            elif cast is not None and cast != target.type:
                err(
                    "MAPPING.INCOMPATIBLE_CAST",
                    f"{path}.as",
                    f"cast {cast!r} is incompatible with property {target.name!r} of type {target.type!r}",
                )
                ok = False
            if has_literal and not _literal_matches(literal, target.type):
                err(
                    "MAPPING.INCOMPATIBLE_LITERAL",
                    f"{path}.literal",
                    f"literal {literal!r} is incompatible with type {target.type!r}",
                )
                ok = False

    if target is None and ok and has_literal and not isinstance(literal, str):
        err(
            "MAPPING.INCOMPATIBLE_LITERAL",
            f"{path}.literal",
            "entity ids and event times are strings; literal must be a string",
        )
        ok = False

    if not ok:
        return None
    return FieldAssignment(field=field_name, literal=literal, cast=cast)


def _literal_matches(value: Any, ptype: str) -> bool:
    expected = _LITERAL_TYPES.get(ptype)
    if expected is None:
        return False
    if isinstance(value, bool) and expected is not bool:
        return expected == bool
    return isinstance(value, expected)


def validate_mapping(
    doc: dict, diag: Diagnostics, file: str, domain: DomainDefinition | None
) -> MappingDefinition | None:
    err = err_at(diag, file)
    warn = warn_at(diag, file)
    start = len(diag.items)
    unknown_keys(doc, {"schema_version", "kind", "mapping"}, err, "")

    section = get_map(doc, "mapping", err, "")
    if section is None:
        return None
    unknown_keys(
        section,
        {"name", "description", "source", "entities", "events"},
        err,
        "mapping",
    )

    name = get_str(section, "name", err, "mapping")
    description = section.get("description")
    if description is not None and not isinstance(description, str):
        err("SCHEMA.INVALID_TYPE", "mapping.description", "'description' must be a string")
        description = None

    source = _validate_source(section.get("source"), err)
    entity_names = list(domain.entities) if domain is not None else []
    event_names = list(domain.events) if domain is not None else []

    bindings: list[EntityBinding] = []
    entities_node = get_list(section, "entities", err, "mapping")
    seen_entities: set[str] = set()
    if entities_node is not None:
        for index, item in enumerate(entities_node):
            path = f"mapping.entities[{index}]"
            if not isinstance(item, dict):
                err("SCHEMA.INVALID_TYPE", path, "entity binding must be a mapping")
                continue
            unknown_keys(item, {"entity", "id", "properties"}, err, path)
            entity_name = get_str(item, "entity", err, path)
            entity_type = None
            if entity_name is not None:
                if entity_name in seen_entities:
                    err(
                        "MAPPING.DUPLICATE_ENTITY",
                        f"{path}.entity",
                        f"entity {entity_name!r} is bound more than once",
                    )
                    entity_name = None
                else:
                    seen_entities.add(entity_name)
                    if domain is not None:
                        entity_type = domain.entities.get(entity_name)
                        if entity_type is None:
                            err(
                                "MAPPING.UNKNOWN_ENTITY",
                                f"{path}.entity",
                                f"unknown entity type {entity_name!r}",
                                hint=suggest(entity_name, entity_names),
                            )
            id_node = item.get("id")
            id_assignment = None
            if id_node is None:
                err("SCHEMA.MISSING_FIELD", f"{path}.id", "entity binding requires 'id'")
            else:
                id_assignment = _validate_assignment(
                    id_node, err, f"{path}.id", None, allowed_casts=None
                )
            prop_assignments: dict[str, FieldAssignment] = {}
            props_node = item.get("properties")
            if props_node is not None:
                if not isinstance(props_node, dict):
                    err("SCHEMA.INVALID_TYPE", f"{path}.properties", "'properties' must be a mapping")
                else:
                    for prop_name, assignment_node in props_node.items():
                        prop_path = f"{path}.properties.{prop_name}"
                        if not isinstance(prop_name, str):
                            err(
                                "SCHEMA.INVALID_TYPE",
                                prop_path,
                                "property name must be a string",
                            )
                            continue
                        target = None
                        if entity_type is not None:
                            target = entity_type.properties.get(prop_name)
                            if target is None:
                                err(
                                    "MAPPING.UNKNOWN_PROPERTY",
                                    prop_path,
                                    f"entity {entity_name!r} has no property {prop_name!r}",
                                    hint=suggest(prop_name, list(entity_type.properties)),
                                )
                                continue
                        result = _validate_assignment(assignment_node, err, prop_path, target)
                        if result is not None:
                            prop_assignments[prop_name] = result
            if entity_type is not None:
                mapped = props_node if isinstance(props_node, dict) else {}
                for prop_name, spec in entity_type.properties.items():
                    if spec.required and prop_name not in mapped:
                        err(
                            "MAPPING.MISSING_REQUIRED",
                            path,
                            f"required property {prop_name!r} of entity "
                            f"{entity_name!r} has no assignment",
                        )
            if entity_name is not None and id_assignment is not None:
                bindings.append(
                    EntityBinding(entity=entity_name, id=id_assignment, properties=prop_assignments)
                )

    event_bindings: list[EventBinding] = []
    events_node = get_list(section, "events", err, "mapping")
    # Event time keys are the names pinned by the domain's explicit policy.
    occurrence_key = (
        domain.policies.event_times.occurrence_field if domain else OCCURRENCE_FIELD
    )
    observation_key = (
        domain.policies.event_times.observation_field if domain else OBSERVATION_FIELD
    )
    if events_node is not None:
        for index, item in enumerate(events_node):
            path = f"mapping.events[{index}]"
            if not isinstance(item, dict):
                err("SCHEMA.INVALID_TYPE", path, "event binding must be a mapping")
                continue
            unknown_keys(
                item,
                {"event", "subject", "when", occurrence_key, observation_key},
                err,
                path,
            )
            event_name = get_str(item, "event", err, path)
            event_type = None
            if event_name is not None and domain is not None:
                event_type = domain.events.get(event_name)
                if event_type is None:
                    err(
                        "MAPPING.UNKNOWN_EVENT",
                        f"{path}.event",
                        f"unknown event type {event_name!r}",
                        hint=suggest(event_name, event_names),
                    )
            subject = _validate_subject(item.get("subject"), err, f"{path}.subject", domain)
            if subject is not None and event_type is not None and subject.entity != event_type.subject:
                err(
                    "MAPPING.SUBJECT_MISMATCH",
                    f"{path}.subject.entity",
                    f"event {event_name!r} has subject {event_type.subject!r}, "
                    f"not {subject.entity!r}",
                )
            when = None
            if "when" in item:
                when = validate_condition(item["when"], err, f"{path}.when")
            occurred_node = item.get(occurrence_key)
            occurred_at = None
            if occurred_node is None:
                err(
                    "SCHEMA.MISSING_FIELD",
                    f"{path}.{occurrence_key}",
                    f"event binding requires '{occurrence_key}'",
                )
            else:
                occurred_at = _validate_assignment(
                    occurred_node,
                    err,
                    f"{path}.{occurrence_key}",
                    None,
                    allowed_casts=TIME_CASTS,
                    require_cast=True,
                )
            observed_at = None
            if observation_key in item:
                observed_at = _validate_assignment(
                    item[observation_key],
                    err,
                    f"{path}.{observation_key}",
                    None,
                    allowed_casts=TIME_CASTS,
                    require_cast=True,
                )
            if event_name is not None and subject is not None and occurred_at is not None:
                event_bindings.append(
                    EventBinding(
                        event=event_name,
                        subject=subject,
                        occurred_at=occurred_at,
                        when=when,
                        observed_at=observed_at,
                    )
                )

    if not bindings and not event_bindings:
        warn("MAPPING.EMPTY", "mapping", "mapping binds no entities and no events")

    own_errors = any(d.severity == "error" for d in diag.items[start:])
    if own_errors or name is None or source is None:
        return None
    return MappingDefinition(
        name=name,
        description=description,
        source=source,
        entities=bindings,
        events=event_bindings,
    )


def _validate_source(node: Any, err) -> SourceSpec | None:
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", "mapping.source", "'source' must be a mapping")
        return None
    unknown_keys(node, {"name", "format"}, err, "mapping.source")
    source_name = get_str(node, "name", err, "mapping.source")
    source_format = get_str(node, "format", err, "mapping.source")
    if source_format is not None and source_format not in SOURCE_FORMATS:
        err(
            "MAPPING.UNKNOWN_FORMAT",
            "mapping.source.format",
            f"unsupported source format {source_format!r}",
            hint=f"supported formats: {', '.join(SOURCE_FORMATS)}",
        )
        source_format = None
    if source_name is None or source_format is None:
        return None
    return SourceSpec(name=source_name, format=source_format)


def _validate_subject(
    node: Any, err, path: str, domain: DomainDefinition | None
) -> SubjectRef | None:
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", path, "'subject' must be a mapping")
        return None
    unknown_keys(node, {"entity", "id"}, err, path)
    entity_name = get_str(node, "entity", err, path)
    if entity_name is not None and domain is not None and entity_name not in domain.entities:
        err(
            "MAPPING.UNKNOWN_ENTITY",
            f"{path}.entity",
            f"unknown entity type {entity_name!r}",
            hint=suggest(entity_name, list(domain.entities)),
        )
    id_node = node.get("id")
    id_assignment = None
    if id_node is None:
        err("SCHEMA.MISSING_FIELD", f"{path}.id", "subject requires 'id'")
    else:
        id_assignment = _validate_assignment(
            id_node, err, f"{path}.id", None, allowed_casts=None
        )
    if entity_name is None or id_assignment is None:
        return None
    return SubjectRef(entity=entity_name, id=id_assignment)
