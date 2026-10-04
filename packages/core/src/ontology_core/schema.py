"""Small helpers for structural validation of parsed YAML documents."""

from __future__ import annotations

import re
from typing import Any, Callable

from .diagnostics import Diagnostics

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ErrorFn = Callable[..., None]


def err_at(diag: Diagnostics, file: str) -> ErrorFn:
    """Bind a diagnostic file so validators can call ``err(code, path, msg)``."""

    def err(code: str, path: str, message: str, hint: str | None = None) -> None:
        diag.error(code, message, file=file, path=path, hint=hint)

    return err


def warn_at(diag: Diagnostics, file: str) -> ErrorFn:
    def warn(code: str, path: str, message: str, hint: str | None = None) -> None:
        diag.warning(code, message, file=file, path=path, hint=hint)

    return warn


def unknown_keys(node: dict, allowed: set[str], err: ErrorFn, path: str) -> None:
    for key in node:
        if key not in allowed:
            err(
                "SCHEMA.UNEXPECTED_KEY",
                f"{path}.{key}" if path else str(key),
                f"unexpected key {key!r}",
                hint=f"allowed keys: {', '.join(sorted(allowed))}",
            )


def get_map(node: dict, key: str, err: ErrorFn, path: str, *, required: bool = True) -> dict | None:
    child_path = f"{path}.{key}" if path else key
    if key not in node:
        if required:
            err("SCHEMA.MISSING_FIELD", child_path, f"missing required field {key!r}")
        return None
    value = node[key]
    if not isinstance(value, dict):
        err("SCHEMA.INVALID_TYPE", child_path, f"expected a mapping, got {_kind(value)}")
        return None
    return value


def get_list(node: dict, key: str, err: ErrorFn, path: str, *, required: bool = True) -> list | None:
    child_path = f"{path}.{key}" if path else key
    if key not in node:
        if required:
            err("SCHEMA.MISSING_FIELD", child_path, f"missing required field {key!r}")
        return None
    value = node[key]
    if not isinstance(value, list):
        err("SCHEMA.INVALID_TYPE", child_path, f"expected a list, got {_kind(value)}")
        return None
    return value


def get_str(node: dict, key: str, err: ErrorFn, path: str, *, required: bool = True) -> str | None:
    child_path = f"{path}.{key}" if path else key
    if key not in node:
        if required:
            err("SCHEMA.MISSING_FIELD", child_path, f"missing required field {key!r}")
        return None
    value = node[key]
    if not isinstance(value, str) or not value:
        err(
            "SCHEMA.INVALID_TYPE",
            child_path,
            f"expected a non-empty string, got {_kind(value)}",
        )
        return None
    return value


def get_num(node: dict, key: str, err: ErrorFn, path: str, *, required: bool = True) -> float | None:
    child_path = f"{path}.{key}" if path else key
    if key not in node:
        if required:
            err("SCHEMA.MISSING_FIELD", child_path, f"missing required field {key!r}")
        return None
    value = node[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        err("SCHEMA.INVALID_TYPE", child_path, f"expected a number, got {_kind(value)}")
        return None
    try:
        return float(value)
    except OverflowError:
        err("SCHEMA.INVALID_TYPE", child_path, "number is too large to represent")
        return None


def get_bool(node: dict, key: str, err: ErrorFn, path: str, *, required: bool = True) -> bool | None:
    child_path = f"{path}.{key}" if path else key
    if key not in node:
        if required:
            err("SCHEMA.MISSING_FIELD", child_path, f"missing required field {key!r}")
        return None
    value = node[key]
    if not isinstance(value, bool):
        err("SCHEMA.INVALID_TYPE", child_path, f"expected a boolean, got {_kind(value)}")
        return None
    return value


def check_name(name: Any, err: ErrorFn, path: str, what: str) -> str | None:
    if not isinstance(name, str) or not name:
        err("SCHEMA.INVALID_TYPE", path, f"{what} name must be a non-empty string")
        return None
    if not NAME_RE.match(name):
        err(
            "SCHEMA.INVALID_NAME",
            path,
            f"{what} name {name!r} must match {NAME_RE.pattern}",
        )
        return None
    return name


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return f"string {value!r}"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return type(value).__name__
