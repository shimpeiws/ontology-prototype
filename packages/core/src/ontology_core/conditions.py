"""Allowlisted declarative condition language.

Conditions are plain data trees evaluated later against source records. They
can never execute Python or SQL: the operator set is closed, operands are
field names and literal scalars, and validation rejects anything else.
"""

from __future__ import annotations

from typing import Any

from .model import Condition
from .schema import ErrorFn

COMPARISON_OPS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte"})
SET_OPS = frozenset({"in", "not_in"})
NULL_OPS = frozenset({"is_null", "not_null"})
LOGIC_OPS = frozenset({"and", "or", "not"})
OPERATORS = COMPARISON_OPS | SET_OPS | NULL_OPS | LOGIC_OPS

MAX_DEPTH = 32

_SCALAR = (str, int, float, bool, type(None))


def validate_condition(node: Any, err: ErrorFn, path: str, _depth: int = 0) -> Condition | None:
    """Validate a condition tree; return it wrapped, or None on errors."""
    if _depth > MAX_DEPTH:
        err("CONDITION.TOO_DEEP", path, f"condition nesting exceeds {MAX_DEPTH} levels")
        return None
    if not isinstance(node, dict):
        err("CONDITION.INVALID_TYPE", path, "condition must be a mapping")
        return None

    op = node.get("op")
    if not isinstance(op, str) or not op:
        err("CONDITION.MISSING_OP", f"{path}.op", "condition requires an 'op' string")
        return None
    if op not in OPERATORS:
        err(
            "CONDITION.UNKNOWN_OP",
            f"{path}.op",
            f"unknown operator {op!r}",
            hint=f"allowed operators: {', '.join(sorted(OPERATORS))}",
        )
        return None

    ok = True

    if op in COMPARISON_OPS:
        ok = _check_keys(node, {"op", "field", "value"}, err, path)
        ok = _check_field(node, err, path) and ok
        if "value" in node and not isinstance(node["value"], _SCALAR):
            err("CONDITION.INVALID_VALUE", f"{path}.value", "value must be a scalar literal")
            ok = False
    elif op in SET_OPS:
        ok = _check_keys(node, {"op", "field", "values"}, err, path)
        ok = _check_field(node, err, path) and ok
        values = node.get("values")
        if "values" in node:
            if not isinstance(values, list) or not values:
                err(
                    "CONDITION.INVALID_VALUE",
                    f"{path}.values",
                    "values must be a non-empty list of scalars",
                )
                ok = False
            elif not all(isinstance(v, _SCALAR) for v in values):
                err("CONDITION.INVALID_VALUE", f"{path}.values", "values must be scalar literals")
                ok = False
    elif op in NULL_OPS:
        ok = _check_keys(node, {"op", "field"}, err, path)
        ok = _check_field(node, err, path) and ok
    elif op in ("and", "or"):
        ok = _check_keys(node, {"op", "args"}, err, path)
        args = node.get("args")
        if "args" in node:
            if not isinstance(args, list) or not args:
                err(
                    "CONDITION.INVALID_VALUE",
                    f"{path}.args",
                    f"'{op}' requires a non-empty list of conditions",
                )
                ok = False
            else:
                for index, arg in enumerate(args):
                    if validate_condition(arg, err, f"{path}.args[{index}]", _depth + 1) is None:
                        ok = False
    else:  # not
        ok = _check_keys(node, {"op", "arg"}, err, path)
        if "arg" in node and validate_condition(node["arg"], err, f"{path}.arg", _depth + 1) is None:
            ok = False

    return Condition(node) if ok else None


def _check_keys(node: dict, allowed: set[str], err: ErrorFn, path: str) -> bool:
    ok = True
    for key in allowed - {"op"}:
        if key not in node:
            err("CONDITION.MISSING_OPERAND", f"{path}.{key}", f"missing operand {key!r}")
            ok = False
    for key in node:
        if key not in allowed:
            err(
                "CONDITION.UNEXPECTED_KEY",
                f"{path}.{key}",
                f"unexpected key {key!r} for operator {node.get('op')!r}",
            )
            ok = False
    return ok


def _check_field(node: dict, err: ErrorFn, path: str) -> bool:
    if "field" not in node:
        return True  # already reported as missing operand
    field = node["field"]
    if not isinstance(field, str) or not field:
        err("CONDITION.INVALID_FIELD", f"{path}.field", "field must be a non-empty string")
        return False
    return True
