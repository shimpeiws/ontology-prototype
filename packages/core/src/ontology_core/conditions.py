"""Allowlisted declarative condition language.

Conditions are plain data trees evaluated later against source records. They
can never execute Python or SQL: the operator set is closed, operands are
field names and literal scalars, and validation rejects anything else.
"""

from __future__ import annotations

import math
import re
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


def evaluate_condition(condition: Condition, record: dict[str, Any]) -> bool:
    """Evaluate a validated condition tree against a normalized record.

    ``record`` holds the normalized scalar/null fields of one source record.
    Comparison rules are deterministic: a ``None`` record value only matches
    ``is_null``/``ne``/``not_in``; literals coerce to the record value's kind
    (numbers compare numerically, ``"true"``/``"false"`` strings compare as
    booleans); ordering operators compare numbers as numbers and strings
    lexicographically, anything else is False.
    """
    return _eval_node(condition.raw, record)


def _eval_node(node: dict, record: dict[str, Any]) -> bool:
    op = node["op"]
    if op == "and":
        return all(_eval_node(arg, record) for arg in node["args"])
    if op == "or":
        return any(_eval_node(arg, record) for arg in node["args"])
    if op == "not":
        return not _eval_node(node["arg"], record)

    value = record.get(node["field"])
    if op == "is_null":
        return value is None
    if op == "not_null":
        return value is not None

    if op in ("in", "not_in"):
        hit = any(_eq(value, item) for item in node["values"])
        return hit if op == "in" else not hit

    target = node["value"]
    if op == "eq":
        return _eq(value, target)
    if op == "ne":
        return not _eq(value, target)
    return _ordered(value, target, op)


_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _num(value: Any) -> float | None:
    """Coerce to a finite number; None when the value is not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            result = float(value)
        except OverflowError:
            # Arbitrary-precision integers that cannot be a float.
            return None
        return result if math.isfinite(result) else None
    if isinstance(value, str) and _NUM_RE.match(value):
        result = float(value)
        return result if math.isfinite(result) else None
    return None


def _bool(value: Any) -> bool | None:
    """Coerce to a boolean; None when the value is not boolean-shaped."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _eq(value: Any, target: Any) -> bool:
    if value is None or target is None:
        return value is None and target is None
    if isinstance(value, str) and isinstance(target, str):
        return value == target
    left_num, right_num = _num(value), _num(target)
    if left_num is not None and right_num is not None:
        return left_num == right_num
    left_bool, right_bool = _bool(value), _bool(target)
    if left_bool is not None and right_bool is not None:
        return left_bool == right_bool
    return False


def _ordered(value: Any, target: Any, op: str) -> bool:
    left_num, right_num = _num(value), _num(target)
    if left_num is not None and right_num is not None:
        pair: Any = (left_num, right_num)
    elif isinstance(value, str) and isinstance(target, str):
        pair = (value, target)
    else:
        return False
    left, right = pair
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    if op == "gt":
        return left > right
    return left >= right
