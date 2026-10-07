"""Tests for scripts/deidentify.py (Issue #58).

Each test names the invariant it pins (INV-001 … INV-009 in the issue plan).
All data here is synthetic.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import shutil
import stat
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import yaml

from ontology_core import ingest_snapshot, load_workspace

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "examples" / "job-invoicing"
FIXED_NOW = dt.datetime(2026, 10, 7, tzinfo=dt.timezone.utc)

_spec = importlib.util.spec_from_file_location("deidentify", REPO_ROOT / "scripts" / "deidentify.py")
deidentify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deidentify)

KEY = bytes(range(32))
JOB_COLUMNS = {
    "id": "pseudonymize",
    "assignee": "pseudonymize",
    "status": "keep",
    "completed_at": "shift",
    "invoiced_at": "shift",
    "notes": "drop",
}

# Sentinels: none of these may survive de-identification (INV-001, INV-008).
MESSY_CSV = (
    "id,status,assignee,completed_at,invoiced_at,notes\n"
    "J-SECRET-101,completed,alice-secret,2026-10-01,,note-secret-a\n"
    '"J-SECRET-102 ",completed,bob-secret,2026-10-02T09:00:00,,note-secret-b\n'
    ",completed,alice-secret,2026-10-01,,\n"
    "J-SECRET-104,completed,carol-secret,1st-october-secret,,\n"
    "J-SECRET-105,completed,alice-secret,2026-10-03,2026-10-05T12:30:00+09:00,,extra-secret\n"
    "J-SECRET-106,in_progress,,,,\n"
)
SENTINELS = [
    "J-SECRET", "alice-secret", "bob-secret", "carol-secret",
    "note-secret", "1st-october-secret", "extra-secret",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-05",
]


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _run(tmp_path: Path, text: str, fmt: str = "csv", columns=None, name="out", key=KEY, shift_days=None):
    source = _write(tmp_path / f"{name}-in.{fmt}", text)
    output = tmp_path / f"{name}.{fmt}"
    deidentify.deidentify_file(source, output, fmt, key, columns or JOB_COLUMNS, shift_days)
    return output.read_text(encoding="utf-8")


def _workspace(tmp_path: Path, name: str, fmt: str = "csv"):
    ws_dir = tmp_path / name
    shutil.copytree(EXAMPLE, ws_dir, ignore=shutil.ignore_patterns(".ontology", "data"))
    if fmt != "csv":
        mapping_path = ws_dir / "mapping.yaml"
        mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        mapping["mapping"]["source"]["format"] = fmt
        mapping_path.write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    return result.workspace


def _ingest_shape(tmp_path: Path, name: str, snapshot: Path, fmt: str):
    result = ingest_snapshot(_workspace(tmp_path, name, fmt), snapshot, observed_at=FIXED_NOW)
    codes = Counter((d.severity, d.code, d.path) for d in result.diagnostics)
    return codes, len(result.accepted), len(result.rejected)


def test_protected_values_never_reach_output(tmp_path: Path) -> None:
    """INV-001: no pseudonymized, shifted, or dropped value appears verbatim."""
    out = _run(tmp_path, MESSY_CSV)
    for sentinel in SENTINELS:
        assert sentinel not in out, sentinel
    assert "completed" in out and "in_progress" in out  # keep columns survive

    records = [
        {"id": "J-SECRET-1", "assignee": "alice-secret", "status": "completed",
         "completed_at": "2026-10-01", "notes": {"x": "note-secret"}},
    ]
    out_json = _run(tmp_path, json.dumps(records), "json", name="json")
    for sentinel in ("J-SECRET", "alice-secret", "note-secret", "2026-10-01"):
        assert sentinel not in out_json


def test_unclassified_column_aborts_without_output(tmp_path: Path) -> None:
    """INV-002: every column is classified, and conflicts are refused."""
    source = _write(tmp_path / "in.csv", "id,status,free_text\n1,completed,hello\n")
    output = tmp_path / "out.csv"
    with pytest.raises(deidentify.DeidentifyError, match="unclassified columns: free_text"):
        deidentify.deidentify_file(source, output, "csv", KEY, {"id": "pseudonymize", "status": "keep"})
    assert not output.exists()

    json_source = _write(tmp_path / "in.json", '[{"id": "1"}, {"id": "2", "late_key": "x"}]')
    with pytest.raises(deidentify.DeidentifyError, match="late_key"):
        deidentify.deidentify_file(json_source, tmp_path / "out.json", "json", KEY, {"id": "keep"})
    assert not (tmp_path / "out.json").exists()

    args = deidentify.main.__globals__["argparse"].Namespace(
        pseudonymize=["id"], shift=None, keep=["id"], drop=None
    )
    with pytest.raises(deidentify.DeidentifyError, match="both pseudonymize and keep"):
        deidentify.parse_columns(args)


def test_pseudonyms_consistent_across_files_and_key_dependent(tmp_path: Path) -> None:
    """INV-003: deterministic per key, consistent across columns/files."""
    jobs = _run(tmp_path, "id,assignee\nJ1,taro\nJ2,hanako\n",
                columns={"id": "pseudonymize", "assignee": "pseudonymize"}, name="jobs")
    people = _run(tmp_path, json.dumps([{"id": "taro"}, {"id": "hanako"}]), "json",
                  columns={"id": "pseudonymize"}, name="people")
    job_rows = list(csv_rows(jobs))
    person_ids = [r["id"] for r in json.loads(people)]
    assert [r["assignee"] for r in job_rows] == person_ids  # refs still resolve
    assert len({r["id"] for r in job_rows} | set(person_ids)) == 4  # distinct stay distinct

    other = _run(tmp_path, "id,assignee\nJ1,taro\nJ2,hanako\n",
                 columns={"id": "pseudonymize", "assignee": "pseudonymize"}, name="other",
                 key=bytes(reversed(range(32))))
    assert [r["id"] for r in csv_rows(other)] != [r["id"] for r in job_rows]


def csv_rows(text: str):
    import csv
    import io
    return csv.DictReader(io.StringIO(text))


def test_date_shift_preserves_intervals_and_value_class(tmp_path: Path) -> None:
    """INV-004: one whole-day offset; value classes and blanks unchanged."""
    tool = deidentify.Deidentifier(KEY, {"t": "shift"}, shift_days=-100)
    assert tool.transform("t", "2026-10-01") == "2026-06-23"
    assert tool.transform("t", "2026-10-01T09:30:00+09:00") == "2026-06-23T09:30:00+09:00"
    assert tool.transform("t", "2026-10-01T09:30:00Z") == "2026-06-23T09:30:00Z"
    assert tool.transform("t", "2026-10-01T09:30:00") == "2026-06-23T09:30:00"  # stays naive
    assert tool.transform("t", " 2026-10-01 ") == " 2026-06-23 "
    assert tool.transform("t", "") == ""
    assert tool.transform("t", "   ") == "   "
    assert tool.transform("t", None) is None
    assert tool.transform("t", "20261001") == deidentify.INVALID_TIMESTAMP
    assert tool.transform("t", "2026-02-30") == deidentify.INVALID_TIMESTAMP
    assert tool.transform("t", 1790000000) == 0

    derived = deidentify.Deidentifier(KEY, {"a": "shift"})
    start, end = dt.date(2026, 9, 30), dt.date(2026, 10, 9)
    shifted_start = dt.date.fromisoformat(derived.transform("a", start.isoformat()))
    shifted_end = dt.date.fromisoformat(derived.transform("a", end.isoformat()))
    assert shifted_end - shifted_start == end - start
    assert shifted_start != start


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_ingest_diagnostics_survive_deidentification(tmp_path: Path, fmt: str) -> None:
    """INV-005: ingestion sees the same findings before and after."""
    if fmt == "csv":
        original = _write(tmp_path / "jobs.csv", MESSY_CSV)
    else:
        original = _write(tmp_path / "jobs.json", json.dumps([
            {"id": "J-1", "status": "completed", "assignee": "taro", "completed_at": "2026-10-01"},
            {"id": 102, "status": "completed", "assignee": "taro", "completed_at": "2026-10-01"},
            {"id": "J-3", "status": "completed", "assignee": {"name": "taro"}, "completed_at": "2026-10-01"},
            {"id": "J-4 ", "status": "completed", "assignee": None, "completed_at": "2026-10-02T09:00:00"},
            {"id": "J-5", "status": "in_progress"},
            ["not", "an", "object"],
        ]))
    deidentified = tmp_path / f"jobs-deid.{fmt}"
    deidentify.deidentify_file(original, deidentified, fmt, KEY, JOB_COLUMNS)

    before = _ingest_shape(tmp_path, "ws-before", original, fmt)
    after = _ingest_shape(tmp_path, "ws-after", deidentified, fmt)
    assert after == before
    codes = {code for (_sev, code, _path) in before[0]}
    expected = {"csv": {"INGEST.SUSPICIOUS_ID", "INGEST.INVALID_TIMESTAMP", "INGEST.MISSING_ID", "INGEST.INVALID_ROW"},
                "json": {"INGEST.INVALID_ID", "INGEST.NON_SCALAR", "INGEST.SUSPICIOUS_ID", "INGEST.INVALID_ROW"}}[fmt]
    assert expected <= codes  # the sample really exercises these findings


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(".ontology/\n", encoding="utf-8")
    (repo / ".ontology").mkdir()
    return repo


def test_refuses_unignored_paths_inside_a_work_tree(tmp_path: Path) -> None:
    """INV-006: output/key paths inside a repo must be ignored; output != input."""
    repo = _git_repo(tmp_path)
    source = _write(tmp_path / "in.csv", "id\nJ1\n")
    columns = {"id": "pseudonymize"}

    with pytest.raises(deidentify.DeidentifyError, match="not ignored"):
        deidentify.deidentify_file(source, repo / "out.csv", "csv", KEY, columns)
    assert not (repo / "out.csv").exists()

    deidentify.deidentify_file(source, repo / ".ontology" / "out.csv", "csv", KEY, columns)
    assert (repo / ".ontology" / "out.csv").exists()

    with pytest.raises(deidentify.DeidentifyError, match="not ignored"):
        deidentify.generate_key(repo / "key.hex")
    assert not (repo / "key.hex").exists()

    with pytest.raises(deidentify.DeidentifyError, match="differ from input"):
        deidentify.deidentify_file(source, source, "csv", KEY, columns)


def test_refuses_when_git_is_unavailable_inside_a_work_tree(tmp_path: Path, monkeypatch) -> None:
    """INV-006: without git, a path inside a work tree is refused, not assumed safe."""
    repo = _git_repo(tmp_path)
    monkeypatch.setattr(deidentify.shutil, "which", lambda _name: None)
    with pytest.raises(deidentify.DeidentifyError, match="git is unavailable"):
        deidentify.ensure_outside_work_tree(repo / ".ontology" / "out.csv", "output")


def test_failure_leaves_no_output_and_existing_output_is_kept(tmp_path: Path) -> None:
    """INV-007: no partial or temp files on failure; never overwrite."""
    bad_json = _write(tmp_path / "bad.json", '[{"id": "J1"}, {"id": ')
    with pytest.raises(deidentify.DeidentifyError, match="not valid JSON"):
        deidentify.deidentify_file(bad_json, tmp_path / "out.json", "json", KEY, {"id": "keep"})
    bad_utf8 = tmp_path / "bad.csv"
    bad_utf8.write_bytes(b"id\nJ\xff1\n")
    with pytest.raises(deidentify.DeidentifyError, match="not valid UTF-8"):
        deidentify.deidentify_file(bad_utf8, tmp_path / "out.csv", "csv", KEY, {"id": "keep"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.csv", "bad.json"]

    existing = _write(tmp_path / "existing.csv", "untouched\n")
    source = _write(tmp_path / "in.csv", "id\nJ1\n")
    with pytest.raises(deidentify.DeidentifyError, match="already exists"):
        deidentify.deidentify_file(source, existing, "csv", KEY, {"id": "keep"})
    assert existing.read_text(encoding="utf-8") == "untouched\n"

    def failing_link(_src, _dst):
        raise FileExistsError
    tool_out = tmp_path / "raced.csv"
    original_link = deidentify.os.link
    deidentify.os.link = failing_link
    try:
        with pytest.raises(deidentify.DeidentifyError, match="already exists"):
            deidentify.deidentify_file(source, tool_out, "csv", KEY, {"id": "keep"})
    finally:
        deidentify.os.link = original_link
    assert not tool_out.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".deidentify-")]


def test_output_streams_never_contain_values_or_key(tmp_path: Path, capsys) -> None:
    """INV-008: stdout/stderr carry no values, no key, no date offset."""
    key_path = tmp_path / "key.hex"
    assert deidentify.main(["--generate-key", str(key_path)]) == 0
    key_text = key_path.read_text(encoding="ascii").strip()
    source = _write(tmp_path / "jobs.csv", MESSY_CSV)
    args = ["--key", str(key_path), "--format", "csv",
            "--pseudonymize", "id,assignee", "--shift", "completed_at,invoiced_at",
            "--keep", "status", "--drop", "notes"]
    assert deidentify.main(args + [str(source), str(tmp_path / "out.csv")]) == 0
    # Error paths: unclassified column, existing output, bad JSON.
    assert deidentify.main(args[:-2] + [str(source), str(tmp_path / "out2.csv")]) == 1
    assert deidentify.main(args + [str(source), str(tmp_path / "out.csv")]) == 1
    bad = _write(tmp_path / "bad.json", '[{"id": "J-SECRET-9", ')
    assert deidentify.main(["--key", str(key_path), "--format", "json", "--keep", "id",
                            str(bad), str(tmp_path / "bad-out.json")]) == 1

    streams = capsys.readouterr()
    printed = streams.out + streams.err
    for sentinel in SENTINELS + [key_text, key_text[:16]]:
        assert sentinel not in printed, sentinel
    offset = deidentify._derived_shift_days(bytes.fromhex(key_text))
    assert str(offset) not in printed and str(-offset) not in printed
    assert "unclassified columns: notes" in streams.err


def test_generate_key_is_private_and_never_overwrites(tmp_path: Path) -> None:
    """INV-009: owner-only permissions; an existing key is never replaced."""
    key_path = tmp_path / "key.hex"
    deidentify.generate_key(key_path)
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    first = key_path.read_bytes()
    assert len(deidentify.read_key(key_path)) == 32
    with pytest.raises(deidentify.DeidentifyError, match="already exists"):
        deidentify.generate_key(key_path)
    assert key_path.read_bytes() == first

    short = _write(tmp_path / "short.hex", "abcd\n")
    with pytest.raises(deidentify.DeidentifyError, match="at least"):
        deidentify.read_key(short)


def test_malformed_csv_is_refused_not_repaired(tmp_path: Path) -> None:
    """FIND-001 (Devin): Given `id,status,completed_at\\n"J1,completed,2026-10-01\\n`,
    ingestion rejects the source as an unfinished quote. De-identification must
    not rewrite it into an ingestible file."""
    text = 'id,status,completed_at\n"J1,completed,2026-10-01\n'
    source = _write(tmp_path / "jobs.csv", text)
    shape = _ingest_shape(tmp_path, "ws-malformed", source, "csv")
    assert ("error", "INGEST.INVALID_SOURCE", None) in shape[0]
    output = tmp_path / "out.csv"
    with pytest.raises(deidentify.DeidentifyError, match="not valid CSV"):
        deidentify.deidentify_file(source, output, "csv", KEY,
                                   {"id": "pseudonymize", "status": "keep", "completed_at": "shift"})
    assert not output.exists()


def test_valid_date_that_cannot_be_shifted_aborts_the_run(tmp_path: Path) -> None:
    """FIND-002 (Devin): With a 100-day backward shift, `0001-01-01` is a valid
    input date, but its de-identified output is `invalid-timestamp` rather than
    a shifted date. A valid timestamp must never become the sentinel."""
    source = _write(tmp_path / "in.csv", "t\n2026-10-01\n0001-01-01\n")
    output = tmp_path / "out.csv"
    with pytest.raises(deidentify.DeidentifyError, match="cannot be shifted"):
        deidentify.deidentify_file(source, output, "csv", KEY, {"t": "shift"}, shift_days=-100)
    assert not output.exists()
    late = _write(tmp_path / "late.csv", "t\n9999-12-01T00:00:00Z\n")
    with pytest.raises(deidentify.DeidentifyError, match="cannot be shifted"):
        deidentify.deidentify_file(late, tmp_path / "late-out.csv", "csv", KEY, {"t": "shift"}, shift_days=100)


@pytest.mark.parametrize("action", ["pseudonymize", "shift", "keep"])
def test_unpaired_surrogate_is_preserved_as_an_encoding_finding(tmp_path: Path, action: str) -> None:
    """FIND-003 (Devin): A record containing `{"id":"\\\\ud800"}` loads as JSON,
    but pseudonymizing `id` raises `UnicodeEncodeError`. It must be handled
    without a traceback, keeping ingestion's ENCODING_ERROR finding."""
    secret = "J-SECRET-\\ud800"
    source = _write(tmp_path / "in.json",
                    '[{"id": "%s", "status": "completed", "completed_at": "2026-10-01"}]' % secret)
    columns = {"id": action, "status": "keep", "completed_at": "shift"}
    output = tmp_path / "out.json"
    deidentify.deidentify_file(source, output, "json", KEY, columns)
    text = output.read_text(encoding="utf-8")
    if action != "keep":
        assert "J-SECRET" not in text
    jobs_columns = dict(JOB_COLUMNS, id=action)
    before = _ingest_shape(tmp_path, f"ws-sur-before-{action}", source, "json")
    deid = tmp_path / f"jobs-{action}.json"
    deidentify.deidentify_file(source, deid, "json", KEY, jobs_columns)
    after = _ingest_shape(tmp_path, f"ws-sur-after-{action}", deid, "json")
    assert ("error", "INGEST.ENCODING_ERROR", "record[1].id") in before[0]
    assert after == before


def test_surrogate_column_name_is_reported_without_crashing(tmp_path: Path, capsys) -> None:
    """FIND-003 sibling: a column name with an unpaired surrogate must not crash
    the error path that prints column names."""
    key_path = tmp_path / "key.hex"
    deidentify.main(["--generate-key", str(key_path)])
    source = _write(tmp_path / "in.json", '[{"id": "J1", "bad\\udc80": "x"}]')
    code = deidentify.main(["--key", str(key_path), "--format", "json", "--keep", "id",
                            str(source), str(tmp_path / "out.json")])
    assert code == 1
    assert "unclassified columns: bad\\udc80" in capsys.readouterr().err
