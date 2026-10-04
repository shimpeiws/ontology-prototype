"""The allowlisted declarative condition language."""

from __future__ import annotations

import pytest

from ontology_core.conditions import MAX_DEPTH, OPERATORS, validate_condition


class _Err:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, code: str, path: str, message: str, hint: str | None = None) -> None:
        self.calls.append((code, path, message))


def _validate(node):
    err = _Err()
    result = validate_condition(node, err, "when")
    return result, [c[0] for c in err.calls]


@pytest.mark.parametrize("op", sorted(OPERATORS - {"and", "or", "not"}))
def test_scalar_ops_accepted(op: str) -> None:
    if op in ("eq", "ne", "lt", "lte", "gt", "gte"):
        node = {"op": op, "field": "status", "value": "completed"}
    elif op in ("in", "not_in"):
        node = {"op": op, "field": "status", "values": ["a", "b"]}
    else:
        node = {"op": op, "field": "status"}
    result, codes = _validate(node)
    assert result is not None, codes
    assert codes == []


@pytest.mark.parametrize("op", ["and", "or"])
def test_logic_ops_accepted(op: str) -> None:
    node = {
        "op": op,
        "args": [
            {"op": "eq", "field": "status", "value": "completed"},
            {"op": "not", "arg": {"op": "is_null", "field": "completed_at"}},
        ],
    }
    result, codes = _validate(node)
    assert result is not None, codes
    assert codes == []


def test_not_accepted() -> None:
    result, codes = _validate({"op": "not", "arg": {"op": "is_null", "field": "x"}})
    assert result is not None
    assert codes == []


def test_unknown_op_rejected() -> None:
    result, codes = _validate({"op": "eval", "field": "x", "value": 1})
    assert result is None
    assert "CONDITION.UNKNOWN_OP" in codes


def test_not_a_mapping_rejected() -> None:
    result, codes = _validate(["op", "eq"])
    assert result is None
    assert "CONDITION.INVALID_TYPE" in codes


def test_missing_op_rejected() -> None:
    result, codes = _validate({"field": "x", "value": 1})
    assert result is None
    assert "CONDITION.MISSING_OP" in codes


def test_missing_operand_rejected() -> None:
    result, codes = _validate({"op": "eq", "field": "x"})
    assert result is None
    assert "CONDITION.MISSING_OPERAND" in codes


def test_extra_key_rejected() -> None:
    result, codes = _validate({"op": "eq", "field": "x", "value": 1, "extra": True})
    assert result is None
    assert "CONDITION.UNEXPECTED_KEY" in codes


def test_non_scalar_value_rejected() -> None:
    result, codes = _validate({"op": "eq", "field": "x", "value": {"nested": 1}})
    assert result is None
    assert "CONDITION.INVALID_VALUE" in codes


def test_empty_values_rejected() -> None:
    result, codes = _validate({"op": "in", "field": "x", "values": []})
    assert result is None
    assert "CONDITION.INVALID_VALUE" in codes


def test_nested_values_rejected() -> None:
    result, codes = _validate({"op": "in", "field": "x", "values": [["a"]]})
    assert result is None
    assert "CONDITION.INVALID_VALUE" in codes


def test_bad_field_rejected() -> None:
    result, codes = _validate({"op": "eq", "field": 3, "value": 1})
    assert result is None
    assert "CONDITION.INVALID_FIELD" in codes


def test_empty_args_rejected() -> None:
    result, codes = _validate({"op": "and", "args": []})
    assert result is None
    assert "CONDITION.INVALID_VALUE" in codes


def test_nested_error_reported_at_path() -> None:
    err = _Err()
    result = validate_condition(
        {"op": "and", "args": [{"op": "bogus"}]}, err, "when"
    )
    assert result is None
    assert any(path == "when.args[0].op" for _, path, _ in err.calls)


def test_depth_cap() -> None:
    node = {"op": "is_null", "field": "x"}
    for _ in range(MAX_DEPTH + 2):
        node = {"op": "not", "arg": node}
    result, codes = _validate(node)
    assert result is None
    assert "CONDITION.TOO_DEEP" in codes


def test_expression_strings_stay_data() -> None:
    # A malicious-looking string is just a literal; nothing is evaluated.
    node = {"op": "eq", "field": "x", "value": "__import__('os').system('id')"}
    result, codes = _validate(node)
    assert result is not None
    assert result.raw["value"] == "__import__('os').system('id')"
    assert codes == []
