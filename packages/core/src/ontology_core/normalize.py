"""Normalization boundary for source records.

Both supported snapshot formats parse into a flat ``dict[str, value]`` of raw
fields: CSV cells arrive as strings (or ``None`` when a row is short), JSON
values arrive typed. Every record then passes through this one boundary,
regardless of format:

- Empty or whitespace-only strings and explicit nulls become ``None``.
- Values keep their scalar type until a mapping assignment applies an
  explicit ``as`` cast; entity ids and ``ref`` properties are never cast.
- ``as: date`` parses a strict ``YYYY-MM-DD`` date. ``as: datetime`` requires an
  explicit UTC offset (``Z`` or ``±HH:MM``) and normalizes to UTC; a date-only
  value in a datetime context means midnight in the workspace
  ``date_only_timezone`` (only ``UTC`` in schema v1). Naive datetimes are
  rejected, never guessed.
- ``nan``/``inf`` are not valid numbers, and ``bool`` is never a number.
- Non-scalar values (nested objects or lists) are rejected.

Nothing here raises on bad input: failures are reported through the ``err``
callback and the caller receives the ``FAILED`` sentinel.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timezone
from typing import Any

from .schema import ErrorFn

# Scalar values a normalized record may hold; ``None`` marks absent/blank.
Scalar = str | int | float | bool
Normalized = Scalar | None

# Sentinel returned when a value fails validation or casting.
FAILED: Any = object()

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

_DATE_ONLY_TIMEZONES = {"UTC": timezone.utc}


def normalize_record(raw: dict[Any, Any], err: ErrorFn, row: int) -> dict[str, Normalized]:
    """Normalize one parsed record into canonical scalar/null fields.

    Applies the format-independent blank/null and scalar rules. ``raw`` comes
    straight from the CSV or JSON parser; field values that are not scalars
    produce ``INGEST.NON_SCALAR`` diagnostics.
    """
    record: dict[str, Normalized] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            # A CSV row with more cells than header columns; reported by the
            # caller as an invalid row, so the extra cells are dropped here.
            continue
        path = f"record[{row}].{key}"
        if isinstance(value, str):
            record[key] = None if value.strip() == "" else value
        elif value is None or isinstance(value, bool):
            record[key] = value
        elif isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                err(
                    "INGEST.INVALID_VALUE",
                    path,
                    "number must be finite; nan/inf are not valid values",
                )
                record[key] = None
            else:
                record[key] = value
        else:
            err(
                "INGEST.NON_SCALAR",
                path,
                f"field value must be a scalar, got {_kind(value)}",
            )
            record[key] = None
    return record


def cast_scalar(
    value: Normalized,
    cast: str,
    err: ErrorFn,
    path: str,
    *,
    date_only_timezone: str = "UTC",
) -> Any:
    """Apply an explicit ``as`` cast to a normalized scalar.

    Returns the typed value, ``None`` for null input, or ``FAILED`` after
    reporting a diagnostic. ``cast`` is one of the schema ``CAST_TYPES``.
    """
    if value is None:
        return None

    if cast == "string":
        if isinstance(value, str):
            return value
        return _fail(err, path, f"expected a string, got {_kind(value)}")

    if cast == "integer":
        if isinstance(value, bool):
            return _fail(err, path, "a boolean is not an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if math.isfinite(value) and value.is_integer():
                return int(value)
            return _fail(err, path, f"expected an integer, got {value!r}")
        if isinstance(value, str) and _INT_RE.match(value):
            try:
                return int(value)
            except ValueError:
                # Digit strings beyond the platform int-string limit.
                pass
        return _fail(err, path, f"expected an integer, got {_kind(value)}")

    if cast == "number":
        if isinstance(value, bool):
            return _fail(err, path, "a boolean is not a number")
        if isinstance(value, (int, float)):
            try:
                result = float(value)
            except OverflowError:
                # JSON integers have arbitrary precision; some are too large
                # to represent as a finite number.
                return _fail(err, path, "number is too large to represent")
            if math.isfinite(result):
                return result
            return _fail(err, path, "number must be finite; nan/inf are not valid values")
        if isinstance(value, str) and _NUM_RE.match(value):
            result = float(value)
            if math.isfinite(result):
                return result
        return _fail(err, path, f"expected a number, got {_kind(value)}")

    if cast == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        return _fail(err, path, f"expected 'true' or 'false', got {_kind(value)}")

    if cast == "date":
        if isinstance(value, str):
            text = value.strip()
            # Strict extended format; datetime strings, basic format
            # (``20261001``), and week dates (``2026-W40-1``) are rejected.
            if _DATE_ONLY_RE.match(text):
                try:
                    return date.fromisoformat(text)
                except ValueError:
                    pass
        return _fail(
            err,
            path,
            f"expected an ISO 8601 date (YYYY-MM-DD), got {_kind(value)}",
            timestamp=True,
        )

    if cast == "datetime":
        if isinstance(value, str):
            text = value.strip()
            if _DATE_ONLY_RE.match(text):
                return datetime.combine(
                    date.fromisoformat(text),
                    time.min,
                    tzinfo=_DATE_ONLY_TIMEZONES.get(date_only_timezone, timezone.utc),
                )
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
                    return _fail(
                        err,
                        path,
                        f"datetime {text!r} has no explicit timezone offset",
                        timestamp=True,
                        hint="use an explicit offset (e.g. 'Z' or '+09:00'); "
                        "date-only values use date_only_timezone",
                    )
                return parsed.astimezone(timezone.utc)
        return _fail(
            err,
            path,
            f"expected an ISO 8601 datetime with an offset, got {_kind(value)}",
            timestamp=True,
        )

    return _fail(err, path, f"unsupported cast {cast!r}")


def as_event_time(value: date | datetime, date_only_timezone: str) -> datetime:
    """Normalize a resolved event-time value to an aware UTC datetime.

    ``as: datetime`` already produced an aware UTC datetime; ``as: date``
    produced a plain date, which means midnight in ``date_only_timezone``.
    """
    if isinstance(value, datetime):
        return value
    return datetime.combine(
        value,
        time.min,
        tzinfo=_DATE_ONLY_TIMEZONES.get(date_only_timezone, timezone.utc),
    )


def _fail(
    err: ErrorFn,
    path: str,
    message: str,
    *,
    timestamp: bool = False,
    hint: str | None = None,
) -> Any:
    code = "INGEST.INVALID_TIMESTAMP" if timestamp else "INGEST.INVALID_VALUE"
    err(code, path, message, hint=hint)
    return FAILED


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return f"number {value!r}"
    if isinstance(value, str):
        return f"string {value!r}"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return type(value).__name__
