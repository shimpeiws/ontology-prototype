"""Validation of the agent task definition (agent.yaml)."""

from __future__ import annotations

from typing import Any

from .diagnostics import Diagnostics
from .domain import suggest, validate_property_spec
from .model import (
    ActionSpec,
    AgentDefinition,
    DomainDefinition,
    PropertySpec,
    TaskSpec,
)
from .schema import check_name, err_at, get_list, get_map, get_str, unknown_keys, warn_at

DELIVERABLES = ("explanation", "hypotheses", "follow_up_questions", "action_proposals")


def validate_agent(
    doc: dict, diag: Diagnostics, file: str, domain: DomainDefinition | None
) -> AgentDefinition | None:
    err = err_at(diag, file)
    warn = warn_at(diag, file)
    start = len(diag.items)
    unknown_keys(doc, {"schema_version", "kind", "agent"}, err, "")

    section = get_map(doc, "agent", err, "")
    if section is None:
        return None
    unknown_keys(
        section,
        {"name", "description", "projection", "task", "permitted_actions"},
        err,
        "agent",
    )

    name = get_str(section, "name", err, "agent")
    description = section.get("description")
    if description is not None and not isinstance(description, str):
        err("SCHEMA.INVALID_TYPE", "agent.description", "'description' must be a string")
        description = None

    projection = get_str(section, "projection", err, "agent")
    if projection is not None and domain is not None and projection not in domain.projections:
        err(
            "AGENT.UNKNOWN_PROJECTION",
            "agent.projection",
            f"unknown projection {projection!r}",
            hint=suggest(projection, list(domain.projections)),
        )

    task = _validate_task(section.get("task"), err)
    actions = _validate_actions(section.get("permitted_actions"), err, domain)

    if task is not None:
        if "action_proposals" in task.deliverables and not actions:
            err(
                "AGENT.NO_ACTIONS",
                "agent.permitted_actions",
                "task deliverables include 'action_proposals' but no permitted actions are declared",
            )
        elif actions and "action_proposals" not in task.deliverables:
            warn(
                "AGENT.ACTIONS_UNREACHABLE",
                "agent.permitted_actions",
                "permitted actions are declared but 'action_proposals' is not a task deliverable",
            )

    own_errors = any(d.severity == "error" for d in diag.items[start:])
    if own_errors or name is None or projection is None or task is None or actions is None:
        return None
    return AgentDefinition(
        name=name,
        description=description,
        projection=projection,
        task=task,
        permitted_actions=actions,
    )


def _validate_task(node: Any, err) -> TaskSpec | None:
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", "agent.task", "'task' must be a mapping")
        return None
    unknown_keys(node, {"goal", "deliverables"}, err, "agent.task")
    goal = get_str(node, "goal", err, "agent.task")
    deliverables_node = get_list(node, "deliverables", err, "agent.task")
    deliverables: list[str] = []
    if deliverables_node is not None:
        if not deliverables_node:
            err(
                "AGENT.EMPTY_DELIVERABLES",
                "agent.task.deliverables",
                "deliverables must be a non-empty list",
            )
        for index, item in enumerate(deliverables_node):
            path = f"agent.task.deliverables[{index}]"
            if not isinstance(item, str):
                err("SCHEMA.INVALID_TYPE", path, "deliverable must be a string")
                continue
            if item not in DELIVERABLES:
                err(
                    "AGENT.UNKNOWN_DELIVERABLE",
                    path,
                    f"unknown deliverable {item!r}",
                    hint=f"allowed: {', '.join(DELIVERABLES)}",
                )
                continue
            if item in deliverables:
                err("AGENT.DUPLICATE_DELIVERABLE", path, f"duplicate deliverable {item!r}")
                continue
            deliverables.append(item)
    if goal is None or not deliverables:
        return None
    return TaskSpec(goal=goal, deliverables=deliverables)


def _validate_actions(
    node: Any, err, domain: DomainDefinition | None
) -> dict[str, ActionSpec] | None:
    if node is None:
        return {}
    if not isinstance(node, dict):
        err("SCHEMA.INVALID_TYPE", "agent.permitted_actions", "'permitted_actions' must be a mapping")
        return None
    actions: dict[str, ActionSpec] = {}
    pending_refs: list[tuple[str, str]] = []
    for action_name, spec in node.items():
        path = f"agent.permitted_actions.{action_name}"
        if check_name(action_name, err, path, "action") is None:
            continue
        if not isinstance(spec, dict):
            err("SCHEMA.INVALID_TYPE", path, "action spec must be a mapping")
            continue
        unknown_keys(spec, {"description", "params"}, err, path)
        action_description = spec.get("description")
        if action_description is not None and not isinstance(action_description, str):
            err("SCHEMA.INVALID_TYPE", f"{path}.description", "'description' must be a string")
            action_description = None
        params: dict[str, PropertySpec] = {}
        params_node = spec.get("params")
        if params_node is not None:
            if not isinstance(params_node, dict):
                err("SCHEMA.INVALID_TYPE", f"{path}.params", "'params' must be a mapping")
            else:
                for param_name, pspec in params_node.items():
                    param_path = f"{path}.params.{param_name}"
                    if check_name(param_name, err, param_path, "param") is None:
                        continue
                    result = validate_property_spec(param_name, pspec, err, param_path, pending_refs)
                    if result is not None:
                        params[param_name] = result
        actions[action_name] = ActionSpec(
            name=action_name, params=params, description=action_description
        )
    if domain is not None:
        entity_names = list(domain.entities)
        for ref_path, target in pending_refs:
            if target not in domain.entities:
                err(
                    "AGENT.UNKNOWN_REF",
                    ref_path,
                    f"action param references unknown entity {target!r}",
                    hint=suggest(target, entity_names),
                )
    return actions
