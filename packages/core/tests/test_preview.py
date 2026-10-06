"""Preview tests: non-persisting mapping preview over in-memory samples.

``preview_snapshot`` must resolve records exactly like ``ingest_snapshot``
(same normalization boundary, same diagnostics) while touching nothing on
disk — no ``.ontology/`` runtime dir, no trace, no canonical store.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import support

from ontology_core import ingest_snapshot, load_workspace, preview_snapshot
from ontology_core.evidence import RUNTIME_DIR
from ontology_core.model import Workspace
from ontology_core.preview import MAX_PREVIEW_BYTES, MAX_PREVIEW_RECORDS

FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

CSV_SAMPLE = (
    "id,status,assignee,amount,completed_at,invoiced_at\n"
    "0101,completed,taro,42.5,2026-10-01,\n"
    "102,in_progress,hanako,,2026-10-02,\n"
)

JSON_SAMPLE = json.dumps(
    [
        {
            "id": "0101",
            "status": "completed",
            "assignee": "taro",
            "amount": 42.5,
            "completed_at": "2026-10-01",
            "invoiced_at": None,
        },
        {
            "id": "102",
            "status": "in_progress",
            "assignee": "hanako",
            "amount": None,
            "completed_at": "2026-10-02",
            "invoiced_at": None,
        },
    ]
)


def make_ws(tmp_path: Path, *, source_format: str = "csv") -> Workspace:
    docs = support.base_docs()
    docs["mapping"]["mapping"]["source"]["format"] = source_format
    ws_dir = support.make_workspace(tmp_path / "ws", docs)
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.workspace is not None
    return result.workspace


def fingerprint(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def codes_of(observation) -> list[str]:
    return [d.code for d in observation.diagnostics]


def test_preview_matches_ingest_resolution(tmp_path: Path) -> None:
    """Same sample, same observed_at: preview resolves identically to ingest."""
    workspace = make_ws(tmp_path)
    preview = preview_snapshot(
        workspace, CSV_SAMPLE.encode(), observed_at=FIXED_NOW
    )
    # Ingest needs the snapshot on disk and writes runtime evidence; run it in
    # a scratch workspace so the comparison stays about semantics.
    scratch = make_ws(tmp_path / "ingest")
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    ingested = ingest_snapshot(scratch, snapshot, observed_at=FIXED_NOW)

    assert preview.ok, [d.format() for d in preview.diagnostics]
    assert len(preview.observations) == len(ingested.observations) == 2
    for prev, ing in zip(preview.observations, ingested.observations):
        assert prev.status == ing.status == "accepted"
        assert prev.row == ing.row
        assert prev.record == ing.record
        assert prev.entities == ing.entities
        assert prev.events == ing.events
        assert codes_of(prev) == codes_of(ing)

    # Explicit versions: input and mapping digests travel with the result.
    assert preview.content_digest == ingested.content_digest
    assert preview.mapping_digest == ingested.mapping_digest


def test_preview_does_not_touch_workspace(tmp_path: Path) -> None:
    ws_dir = tmp_path / "ws"
    workspace = make_ws(tmp_path)
    before = fingerprint(ws_dir)

    result = preview_snapshot(workspace, CSV_SAMPLE.encode(), observed_at=FIXED_NOW)

    assert result.ok
    assert len(result.accepted) == 2
    assert fingerprint(ws_dir) == before
    assert not (ws_dir / RUNTIME_DIR).exists()


def test_preview_csv_and_json_resolve_identically(tmp_path: Path) -> None:
    csv_ws = make_ws(tmp_path / "csv", source_format="csv")
    json_ws = make_ws(tmp_path / "json", source_format="json")

    csv_result = preview_snapshot(csv_ws, CSV_SAMPLE.encode(), observed_at=FIXED_NOW)
    json_result = preview_snapshot(
        json_ws, JSON_SAMPLE.encode(), observed_at=FIXED_NOW
    )

    assert csv_result.ok and json_result.ok
    for csv_obs, json_obs in zip(csv_result.observations, json_result.observations):
        assert set(csv_obs.record) == set(json_obs.record)
        assert csv_obs.entities == json_obs.entities
        assert csv_obs.events == json_obs.events


def test_preview_row_diagnostics(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    sample = (
        "id,status,assignee,completed_at\n"
        ",completed,taro,2026-10-01\n"
        "102,completed,hanako,yesterday\n"
    )

    result = preview_snapshot(workspace, sample.encode(), observed_at=FIXED_NOW)

    assert not result.ok
    assert [o.status for o in result.observations] == ["rejected", "rejected"]
    assert "INGEST.MISSING_ID" in codes_of(result.observations[0])
    assert "INGEST.INVALID_TIMESTAMP" in codes_of(result.observations[1])
    # A rejected row emits no partially mapped facts.
    assert all(
        not o.entities and not o.events for o in result.observations
    )


def test_preview_sample_too_large(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    oversize = b"id\n" + b"1\n" * MAX_PREVIEW_BYTES

    result = preview_snapshot(workspace, oversize, observed_at=FIXED_NOW)

    assert not result.ok
    assert result.observations == []
    assert [d.code for d in result.diagnostics] == ["PREVIEW.SAMPLE_TOO_LARGE"]


def test_preview_truncates_long_samples(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    rows = ["id,status,assignee,completed_at,invoiced_at"]
    rows += [
        f"{i},completed,taro,2026-10-01,"
        for i in range(MAX_PREVIEW_RECORDS + 10)
    ]
    sample = "\n".join(rows) + "\n"

    result = preview_snapshot(workspace, sample.encode(), observed_at=FIXED_NOW)

    assert result.truncated is True
    assert result.record_count == MAX_PREVIEW_RECORDS + 10
    assert len(result.observations) == MAX_PREVIEW_RECORDS
    assert "PREVIEW.TRUNCATED" in [d.code for d in result.diagnostics]


def test_preview_rejects_invalid_encoding(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)

    result = preview_snapshot(workspace, b"\xff\xfe", observed_at=FIXED_NOW)

    assert not result.ok
    assert [d.code for d in result.diagnostics] == ["INGEST.ENCODING_ERROR"]


def test_preview_rejects_unparseable_source(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path / "json", source_format="json")

    result = preview_snapshot(
        workspace, b'{"not": "a list"}', observed_at=FIXED_NOW
    )

    assert not result.ok
    assert [d.code for d in result.diagnostics] == ["INGEST.INVALID_SOURCE"]


def test_preview_empty_sample(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)

    result = preview_snapshot(
        workspace, b"id,status,assignee\n", observed_at=FIXED_NOW
    )

    assert result.ok
    assert result.observations == []
    assert "INGEST.EMPTY" in [d.code for d in result.diagnostics]


def test_preview_ids_are_deterministic_and_versioned(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)

    first = preview_snapshot(workspace, CSV_SAMPLE.encode(), observed_at=FIXED_NOW)
    second = preview_snapshot(workspace, CSV_SAMPLE.encode(), observed_at=FIXED_NOW)

    assert first.preview_id is not None
    assert first.preview_id == second.preview_id
    assert [o.observation_id for o in first.observations] == [
        o.observation_id for o in second.observations
    ]

    changed = preview_snapshot(
        workspace, CSV_SAMPLE.replace("taro", "jiro").encode(),
        observed_at=FIXED_NOW,
    )
    assert changed.preview_id != first.preview_id


def test_preview_ids_cover_domain_only_changes(tmp_path: Path) -> None:
    """Resolution consults the domain, so a domain-only edit changes ids."""
    workspace = make_ws(tmp_path / "strict")
    # A blank `status` rejects the row while the property is required.
    sample = b"id,status,assignee,completed_at\n101,,taro,2026-10-01\n"
    strict = preview_snapshot(workspace, sample, observed_at=FIXED_NOW)
    assert strict.observations[0].status == "rejected"
    assert "INGEST.MISSING_REQUIRED" in codes_of(strict.observations[0])

    docs = support.base_docs()
    job_props = docs["domain"]["domain"]["entities"]["Job"]["properties"]
    job_props["status"]["required"] = False
    relaxed_dir = support.make_workspace(tmp_path / "relaxed", docs)
    loaded = load_workspace(relaxed_dir)
    assert loaded.ok and loaded.workspace is not None
    relaxed = preview_snapshot(loaded.workspace, sample, observed_at=FIXED_NOW)

    assert relaxed.observations[0].status == "accepted"
    # Same input, same mapping — but different observations under the same
    # preview_id would mislabel evidence, so the domain feeds the ids.
    assert relaxed.mapping_digest == strict.mapping_digest
    assert relaxed.preview_id != strict.preview_id
    assert (
        relaxed.observations[0].observation_id
        != strict.observations[0].observation_id
    )


def test_preview_escaped_json_surrogate_is_a_diagnostic(tmp_path: Path) -> None:
    """A ``\\ud800`` escape decodes to a lone surrogate inside a record.

    The surrogate cannot be encoded back to UTF-8, so it must surface as a
    record diagnostic — not leak into an observation and crash response
    serialization downstream.
    """
    workspace = make_ws(tmp_path / "json", source_format="json")
    sample = (
        b'[{"id": "\\ud800", "status": "completed", "assignee": "taro",'
        b' "completed_at": "2026-10-01"}]'
    )

    result = preview_snapshot(workspace, sample, observed_at=FIXED_NOW)

    assert not result.ok
    observation = result.observations[0]
    assert observation.status == "rejected"
    assert "INGEST.ENCODING_ERROR" in codes_of(observation)
    assert observation.record["id"] is None
