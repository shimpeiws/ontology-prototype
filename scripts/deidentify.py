#!/usr/bin/env python3
"""De-identify a CSV/JSON operational snapshot for a local applicability run.

Every column must be classified explicitly:

- ``--pseudonymize``: replace each value with a keyed HMAC pseudonym. Equal
  values map to equal pseudonyms across columns and files that share a key,
  so references (``assignee`` -> ``Person.id``) still resolve.
- ``--shift``: move every date/datetime by the same whole-day offset, so
  intervals such as completion-to-invoice latency are unchanged.
- ``--keep``: copy verbatim. Use only for non-identifying values (statuses,
  flags); the operator decides what is safe to keep.
- ``--drop``: remove the column.

The output keeps what ingestion diagnoses — blanks, surrounding whitespace on
ids, JSON value types, naive timestamps, nested values, extra CSV cells — so
findings on the de-identified sample match the original. The key, the date
offset, and input values are never printed. See docs/real-data-validation.md.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ACTIONS = ("pseudonymize", "shift", "keep", "drop")
INVALID_TIMESTAMP = "invalid-timestamp"
# Ingestion reports any string with an unpaired surrogate as
# INGEST.ENCODING_ERROR; this content-free stand-in keeps that finding.
SURROGATE_PLACEHOLDER = "\ud800"
MIN_KEY_BYTES = 16
BOM = "﻿"

_EDGE_SPACE = re.compile(r"^(\s*)(.*?)(\s*)$", re.S)
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
_DATETIME = re.compile(r"^(\d{4}-\d{2}-\d{2})([Tt ].+)$", re.S)


class DeidentifyError(Exception):
    """A refusal or failure. Messages name columns and positions, never values."""


class Deidentifier:
    def __init__(self, key: bytes, columns: dict[str, str], shift_days: int | None = None):
        self._key = key
        self._columns = columns
        self._shift = dt.timedelta(
            days=shift_days if shift_days is not None else _derived_shift_days(key)
        )
        self._seen: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def pseudonym(self, raw: str) -> str:
        digest = hmac.new(self._key, b"pseudonym:v1:" + raw.encode("utf-8"), hashlib.sha256)
        token = "p" + digest.hexdigest()[:16]
        previous = self._seen.setdefault(token, raw)
        if previous != raw:
            raise DeidentifyError("pseudonym collision; use a different key")
        return token

    def _pseudonymize(self, value: Any) -> Any:
        if isinstance(value, str):
            lead, core, trail = _EDGE_SPACE.match(value).groups()
            # Whitespace-only stays as is (ingestion reads it as null); the
            # surrounding whitespace survives so INGEST.SUSPICIOUS_ID does too.
            return value if not core else lead + self.pseudonym(core) + trail
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            # A numeric id stays numeric, so INGEST.INVALID_ID is preserved.
            return int(self.pseudonym(json.dumps(value))[1:13], 16)
        return _emptied(value)

    def _shift_value(self, column: str, value: Any) -> Any:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return 0
        if not isinstance(value, str):
            return _emptied(value)
        lead, core, trail = _EDGE_SPACE.match(value).groups()
        if not core:
            return value
        match = _DATE.match(core) or _DATETIME.match(core)
        if match is None:
            return lead + INVALID_TIMESTAMP + trail
        try:
            day = dt.date.fromisoformat(match.group(1))
        except ValueError:
            return lead + INVALID_TIMESTAMP + trail
        try:
            shifted = (day + self._shift).isoformat()
        except OverflowError:
            # A valid date must never become the malformed-value sentinel.
            raise DeidentifyError(
                f"a valid date in column {column!r} cannot be shifted within the "
                "representable range; pass a different --shift-days"
            ) from None
        # Only the date moves: time of day, offset, and naive-ness stay.
        rest = match.group(2) if match.re is _DATETIME else ""
        return lead + shifted + rest + trail

    def transform(self, column: str, value: Any) -> Any:
        action = self._columns[column]
        if action != "keep" and isinstance(value, str) and _has_unpaired_surrogate(value):
            result = SURROGATE_PLACEHOLDER
        elif action == "keep":
            result = _emptied(value) if isinstance(value, (dict, list)) else value
        elif action == "pseudonymize":
            result = self._pseudonymize(value)
        else:
            result = self._shift_value(column, value)
        self.counts[column] = self.counts.get(column, 0) + 1
        return result

    def check_columns(self, names: list[str]) -> None:
        unknown = sorted({n for n in names if n not in self._columns})
        if unknown:
            raise DeidentifyError(
                "unclassified columns: " + ", ".join(_label(n) for n in unknown)
                + " (classify each as --pseudonymize, --shift, --keep, or --drop)"
            )

    def csv_text(self, text: str) -> tuple[str, int]:
        bom = text.startswith(BOM)
        # strict=True matches ingestion's parser: a source it rejects (an
        # unterminated quote) is refused here, never rewritten into valid CSV.
        reader = csv.reader(io.StringIO(text[1:] if bom else text, newline=""), strict=True)
        try:
            rows = list(reader)
        except csv.Error:
            raise DeidentifyError(f"input is not valid CSV (line {reader.line_num})") from None
        if not rows:
            return text, 0
        header = rows[0]
        self.check_columns(header)
        kept = [i for i, name in enumerate(header) if self._columns[name] != "drop"]
        out = io.StringIO(newline="")
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow([header[i] for i in kept])
        for row in rows[1:]:
            cells = [self.transform(header[i], row[i]) for i in kept if i < len(row)]
            # Extra cells beyond the header keep their count, not their content.
            cells += [""] * max(0, len(row) - len(header))
            writer.writerow(cells)
        return (BOM if bom else "") + out.getvalue(), len(rows) - 1

    def json_text(self, text: str) -> tuple[str, int]:
        try:
            document = json.loads(text, parse_constant=_reject_constant)
        except ValueError as exc:
            position = getattr(exc, "pos", None)
            where = f" at character {position}" if position is not None else ""
            raise DeidentifyError(f"input is not valid JSON{where}") from None
        if not isinstance(document, list):
            raise DeidentifyError("JSON input must be a top-level array of records")
        names = [k for item in document if isinstance(item, dict) for k in item]
        self.check_columns(names)
        records = []
        for item in document:
            if not isinstance(item, dict):
                records.append([] if isinstance(item, list) else None)
                continue
            records.append(
                {k: self.transform(k, v) for k, v in item.items() if self._columns[k] != "drop"}
            )
        # ASCII escapes keep kept surrogates (and the placeholder) writable.
        return json.dumps(records, ensure_ascii=True, indent=2) + "\n", len(document)


def _has_unpaired_surrogate(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def _label(name: str) -> str:
    """A column name safe to print (unpaired surrogates escaped)."""
    return ascii(name)[1:-1] if _has_unpaired_surrogate(name) else name


def _emptied(value: Any) -> Any:
    """A nested value keeps its kind (INGEST.NON_SCALAR) but loses its content."""
    return {} if isinstance(value, dict) else []


def _reject_constant(value: str) -> Any:
    raise ValueError("non-finite JSON constant")


def _derived_shift_days(key: bytes) -> int:
    digest = hmac.new(key, b"date-shift:v1", hashlib.sha256).digest()
    return -(30 + int.from_bytes(digest[:4], "big") % 700)


def parse_columns(args: argparse.Namespace) -> dict[str, str]:
    columns: dict[str, str] = {}
    for action in ACTIONS:
        for group in getattr(args, action) or []:
            for name in (n.strip() for n in group.split(",")):
                if not name:
                    continue
                if name in columns and columns[name] != action:
                    raise DeidentifyError(
                        f"column {name!r} is classified as both {columns[name]} and {action}"
                    )
                columns[name] = action
    return columns


def ensure_outside_work_tree(path: Path, role: str) -> None:
    """Refuse a path inside a git work tree unless git reports it ignored."""
    target = path.resolve()
    if not any((parent / ".git").exists() for parent in target.parents):
        return
    git = shutil.which("git")
    if git is None:
        raise DeidentifyError(f"{role} is inside a git work tree and git is unavailable to check it")
    try:
        top = subprocess.run(
            [git, "-C", str(target.parent), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
        if top.returncode != 0:
            raise DeidentifyError(f"{role}: cannot determine the enclosing git work tree")
        ignored = subprocess.run(
            [git, "-C", top.stdout.strip(), "check-ignore", "-q", str(target)],
            capture_output=True, check=False,
        )
    except OSError:
        raise DeidentifyError(f"{role}: git could not be run to check the path") from None
    if ignored.returncode == 0:
        return
    if ignored.returncode == 1:
        raise DeidentifyError(
            f"{role} is inside a git work tree and not ignored; write it outside the repository"
        )
    raise DeidentifyError(f"{role}: git check-ignore failed")


def write_exclusive(path: Path, data: str) -> None:
    """Create ``path`` atomically; never overwrite and never leave a partial file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".deidentify-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise DeidentifyError("output already exists; choose a new path") from None
    finally:
        os.unlink(tmp)


def generate_key(path: Path) -> None:
    ensure_outside_work_tree(path, "key file")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise DeidentifyError("key file already exists; refusing to overwrite it") from None
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(secrets.token_hex(32) + "\n")


def read_key(path: Path) -> bytes:
    ensure_outside_work_tree(path, "key file")
    try:
        text = path.read_text(encoding="ascii").strip()
        key = bytes.fromhex(text)
    except (OSError, UnicodeDecodeError, ValueError):
        raise DeidentifyError("cannot read key file (expected hex from --generate-key)") from None
    if len(key) < MIN_KEY_BYTES:
        raise DeidentifyError(f"key must be at least {MIN_KEY_BYTES} bytes")
    return key


def deidentify_file(
    source: Path, output: Path, fmt: str, key: bytes, columns: dict[str, str],
    shift_days: int | None = None,
) -> Deidentifier:
    if output.resolve() == source.resolve():
        raise DeidentifyError("output must differ from input")
    if not output.parent.is_dir():
        raise DeidentifyError("output directory does not exist")
    ensure_outside_work_tree(output, "output")
    if output.exists():
        raise DeidentifyError("output already exists; choose a new path")
    try:
        text = source.read_bytes().decode("utf-8")
    except OSError as exc:
        raise DeidentifyError(f"cannot read input: {exc.strerror or 'I/O error'}") from None
    except UnicodeDecodeError as exc:
        raise DeidentifyError(f"input is not valid UTF-8 (byte offset {exc.start})") from None
    tool = Deidentifier(key, columns, shift_days)
    data, _records = tool.csv_text(text) if fmt == "csv" else tool.json_text(text)
    try:
        write_exclusive(output, data)
    except OSError as exc:
        raise DeidentifyError(f"cannot write output: {exc.strerror or 'I/O error'}") from None
    return tool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--generate-key", type=Path, metavar="PATH",
                        help="create a new random key file (owner-only) and exit")
    parser.add_argument("--key", type=Path, help="key file from --generate-key")
    parser.add_argument("--format", choices=("csv", "json"),
                        help="snapshot format (the format of the selected mapping source)")
    for action in ACTIONS:
        parser.add_argument(f"--{action}", action="append", metavar="COLS",
                            help=f"comma-separated columns to {action}")
    parser.add_argument("--shift-days", type=int,
                        help="explicit day offset (default: derived from the key)")
    parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args(argv)

    try:
        if args.generate_key is not None:
            generate_key(args.generate_key)
            print("created key file (keep it outside the repository; delete it after the run)")
            return 0
        if not (args.key and args.format and args.input and args.output):
            parser.error("--key, --format, input, and output are required")
        columns = parse_columns(args)
        tool = deidentify_file(args.input, args.output, args.format,
                               read_key(args.key), columns, args.shift_days)
    except DeidentifyError as exc:
        print(f"deidentify: {exc}", file=sys.stderr)
        return 1

    print(f"deidentified {args.format} snapshot; values per column:")
    for name, action in sorted(columns.items()):
        print(f"  {_label(name)}: {action} ({tool.counts.get(name, 0) if action != 'drop' else 'removed'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
