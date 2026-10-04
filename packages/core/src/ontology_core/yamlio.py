"""YAML loading for workspace definitions.

Loading is data-only: a restricted SafeLoader rejects Python object tags,
performs no environment interpolation, and reports duplicate mapping keys
instead of silently keeping the last value. Anchors/aliases are permitted;
a merge key that overrides an explicit key is reported as a duplicate.
Files are UTF-8 only and capped in size; every failure mode — including
parser-level recursion or allocation errors — becomes a diagnostic rather
than an exception.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from yaml.nodes import MappingNode

from .diagnostics import Diagnostics

MAX_FILE_BYTES = 1_000_000


class DuplicateKeyError(Exception):
    def __init__(self, key: str, line: int | None) -> None:
        super().__init__(f"duplicate key {key!r}")
        self.key = key
        self.line = line


class _WorkspaceLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        mapping: dict = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in mapping
            except TypeError:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found unhashable key",
                    key_node.start_mark,
                ) from None
            if duplicate:
                raise DuplicateKeyError(
                    str(key), getattr(key_node.start_mark, "line", 0) + 1
                )
            mapping[key] = self.construct_object(value_node, deep=True)
        return mapping


def load_yaml_file(path: Path, file: str, diag: Diagnostics) -> dict | None:
    """Read one workspace YAML file into a top-level mapping, or None.

    Every failure is reported to ``diag``; this function never raises.
    """
    try:
        size = path.stat().st_size
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except OSError as exc:
        diag.error(
            "WORKSPACE.UNREADABLE_FILE",
            f"cannot read file: {exc.strerror or exc}",
            file=file,
        )
        return None
    except UnicodeDecodeError as exc:
        diag.error(
            "WORKSPACE.ENCODING_ERROR",
            f"file is not valid UTF-8: {exc}",
            file=file,
        )
        return None

    if size > MAX_FILE_BYTES:
        diag.error(
            "WORKSPACE.FILE_TOO_LARGE",
            f"file is {size} bytes; limit is {MAX_FILE_BYTES}",
            file=file,
        )
        return None

    try:
        document = yaml.load(text, Loader=_WorkspaceLoader)
    except DuplicateKeyError as exc:
        diag.error(
            "WORKSPACE.DUPLICATE_KEY",
            f"duplicate mapping key {exc.key!r}",
            file=file,
            line=exc.line,
            hint="rename or remove one of the duplicate keys",
        )
        return None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if getattr(mark, "line", None) is not None else None
        problem = getattr(exc, "problem", None) or "invalid YAML"
        diag.error(
            "WORKSPACE.YAML_ERROR",
            f"invalid YAML: {problem}",
            file=file,
            line=line,
        )
        return None
    except Exception as exc:
        diag.error(
            "WORKSPACE.YAML_ERROR",
            f"cannot parse YAML: {type(exc).__name__}",
            file=file,
        )
        return None

    if document is None:
        diag.error("WORKSPACE.EMPTY_FILE", "file is empty", file=file)
        return None
    if not isinstance(document, dict):
        diag.error(
            "SCHEMA.INVALID_DOCUMENT",
            "document must be a YAML mapping",
            file=file,
        )
        return None
    return document
