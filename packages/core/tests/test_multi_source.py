"""Multi-source mappings: validation, source selection, cross-source refs.

``mapping.sources[]`` lets several systems populate the same canonical
model. These tests pin: the sources-form schema and its mutual exclusion
with the legacy single-source form, explicit (never guessed) source
selection at ingest and preview, per-source bindings and formats, and
exact stable-id-only cross-source reference resolution — unresolved
references are warnings, never rejections or fabrications.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import support

from ontology_core import (
    ingest_snapshot,
    load_workspace,
    preview_snapshot,
)

FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

JOBS_CSV = (
    "id,status,assignee,amount,completed_at,invoiced_at\n"
    "J01,completed,taro,42.5,2026-10-01,\n"
    "J02,in_progress,hanako,,2026-10-02,\n"
)

INVOICES_CSV = (
    "id,job,issued_at\n"
    "INV-1,J01,2026-10-05\n"
    "INV-2,J02,2026-10-06\n"
)


def multi_docs() -> dict[str, dict]:
    """The base single-source docs, redeclared as a two-source mapping."""
    docs = support.base_docs()
    mapping = docs["mapping"]["mapping"]
    docs["mapping"]["mapping"] = {
        "name": "jobs-invoices",
        "sources": [
            {
                "name": "jobs",
                "format": "csv",
                "entities": mapping["entities"],
                "events": mapping["events"],
            },
            {
                "name": "invoices",
                "format": "csv",
                "entities": [
                    {
                        "entity": "Invoice",
                        "id": {"field": "id"},
                        "properties": {"job": {"field": "job"}},
                    }
                ],
                "events": [
                    {
                        "event": "InvoiceIssued",
                        "subject": {
                            "entity": "Job",
                            "id": {"field": "job"},
                        },
                        "occurred_at": {
                            "field": "issued_at",
                            "as": "date",
                        },
                    }
                ],
            },
        ],
    }
    return docs


def make_multi(tmp_path: Path, docs: dict | None = None):
    ws_dir = support.make_workspace(tmp_path / "ws", docs or multi_docs())
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.workspace is not None
    return result.workspace


def write_snapshot(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def codes_of(observation) -> list[str]:
    return [d.code for d in observation.diagnostics]


# --- mapping.yaml validation -------------------------------------------


def test_sources_form_parses_to_typed_model(tmp_path: Path) -> None:
    workspace = make_multi(tmp_path)
    assert [s.name for s in workspace.mapping.sources] == ["jobs", "invoices"]
    assert workspace.mapping.sources[0].format == "csv"
    assert [b.entity for b in workspace.mapping.sources[1].entities] == [
        "Invoice"
    ]
    assert [b.event for b in workspace.mapping.sources[1].events] == [
        "InvoiceIssued"
    ]


def test_legacy_form_normalizes_to_one_source(tmp_path: Path) -> None:
    ws_dir = support.make_workspace(tmp_path)
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    sources = result.workspace.mapping.sources
    assert len(sources) == 1
    assert sources[0].name == "jobs"
    assert sources[0].format == "csv"
    assert [b.entity for b in sources[0].entities] == ["Job", "Person"]


def test_source_and_sources_together_rejected(tmp_path: Path) -> None:
    docs = multi_docs()
    docs["mapping"]["mapping"]["source"] = {"name": "jobs", "format": "csv"}
    result = load_workspace(support.make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.MIXED_SOURCES" in support.codes(result)


def test_sources_with_top_level_bindings_rejected(tmp_path: Path) -> None:
    docs = multi_docs()
    docs["mapping"]["mapping"]["entities"] = []
    result = load_workspace(support.make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.MIXED_SOURCES" in support.codes(result)


def test_empty_sources_rejected(tmp_path: Path) -> None:
    docs = multi_docs()
    docs["mapping"]["mapping"]["sources"] = []
    result = load_workspace(support.make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "MAPPING.NO_SOURCES")
    assert diag.path == "mapping.sources"


def test_duplicate_source_names_rejected(tmp_path: Path) -> None:
    docs = multi_docs()
    dup = dict(docs["mapping"]["mapping"]["sources"][0])
    docs["mapping"]["mapping"]["sources"].append(dup)
    result = load_workspace(support.make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(
        d for d in result.diagnostics if d.code == "MAPPING.DUPLICATE_SOURCE"
    )
    assert diag.path == "mapping.sources[2].name"


def test_source_entry_unknown_format_rejected(tmp_path: Path) -> None:
    docs = multi_docs()
    docs["mapping"]["mapping"]["sources"][1]["format"] = "xml"
    result = load_workspace(support.make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(
        d for d in result.diagnostics if d.code == "MAPPING.UNKNOWN_FORMAT"
    )
    assert diag.path == "mapping.sources[1].format"


def test_bindings_without_any_source_rejected(tmp_path: Path) -> None:
    # A mapping that declares bindings but neither `source` nor `sources`
    # reports SCHEMA.MISSING_FIELD at mapping.source — pinned since the
    # sources change switched this path from SCHEMA.INVALID_TYPE.
    docs = support.base_docs()
    del docs["mapping"]["mapping"]["source"]
    result = load_workspace(support.make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(
        d for d in result.diagnostics if d.code == "SCHEMA.MISSING_FIELD"
    )
    assert diag.path == "mapping.source"


def test_source_entry_missing_name_rejected(tmp_path: Path) -> None:
    docs = multi_docs()
    del docs["mapping"]["mapping"]["sources"][0]["name"]
    result = load_workspace(support.make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.MISSING_FIELD" in support.codes(result)


def test_nested_binding_diagnostics_carry_source_path(tmp_path: Path) -> None:
    docs = multi_docs()
    docs["mapping"]["mapping"]["sources"][1]["entities"][0]["entity"] = "Jbo"
    result = load_workspace(support.make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(
        d for d in result.diagnostics if d.code == "MAPPING.UNKNOWN_ENTITY"
    )
    assert diag.path == "mapping.sources[1].entities[0].entity"


# --- source selection at ingest ----------------------------------------


def test_ingest_requires_source_on_multi_source(tmp_path: Path) -> None:
    workspace = make_multi(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    assert result.observations == []
    diag = next(
        d for d in result.diagnostics if d.code == "INGEST.SOURCE_REQUIRED"
    )
    assert "jobs" in diag.message and "invoices" in diag.message


def test_ingest_unknown_source_is_a_diagnostic(tmp_path: Path) -> None:
    workspace = make_multi(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    result = ingest_snapshot(
        workspace, snapshot, source="bogus", observed_at=FIXED_NOW
    )

    assert not result.ok
    assert result.observations == []
    diag = next(
        d for d in result.diagnostics if d.code == "INGEST.UNKNOWN_SOURCE"
    )
    assert "bogus" in diag.message
    assert "jobs" in (diag.hint or "")


def test_ingest_legacy_single_source_needs_no_flag(tmp_path: Path) -> None:
    ws_dir = support.make_workspace(tmp_path)
    workspace = load_workspace(ws_dir).workspace
    assert workspace is not None
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.source == "jobs"


def test_per_source_bindings_and_evidence(tmp_path: Path) -> None:
    workspace = make_multi(tmp_path)
    jobs = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)
    invoices = write_snapshot(tmp_path, "invoices.csv", INVOICES_CSV)

    jobs_result = ingest_snapshot(
        workspace, jobs, source="jobs", observed_at=FIXED_NOW
    )
    assert jobs_result.ok
    assert all(o.source == "jobs" for o in jobs_result.observations)

    invoices_result = ingest_snapshot(
        workspace, invoices, source="invoices", observed_at=FIXED_NOW
    )
    assert invoices_result.ok, [
        d.format() for d in invoices_result.diagnostics
    ]
    first = invoices_result.observations[0]
    assert first.source == "invoices"
    invoice = next(e for e in first.entities if e.entity == "Invoice")
    assert invoice.id == "INV-1"
    assert invoice.properties["job"] == "J01"


def test_unresolved_refs_warn_never_reject(tmp_path: Path) -> None:
    # Invoices first, no Job entities known: both the `job` ref property
    # and the InvoiceIssued subject warn — the observation still accepts.
    workspace = make_multi(tmp_path)
    invoices = write_snapshot(tmp_path, "invoices.csv", INVOICES_CSV)

    result = ingest_snapshot(
        workspace, invoices, source="invoices", observed_at=FIXED_NOW
    )

    assert result.ok, [d.format() for d in result.diagnostics]
    assert all(o.status == "accepted" for o in result.observations)
    first = result.observations[0]
    unresolved = [
        d for d in first.diagnostics if d.code == "INGEST.UNRESOLVED_REF"
    ]
    assert len(unresolved) == 2  # property ref + event subject
    assert all(d.severity == "warning" for d in unresolved)
    assert any("Job" in d.message and "J01" in d.message for d in unresolved)


def test_refs_resolve_across_sources_in_canonical(tmp_path: Path) -> None:
    workspace = make_multi(tmp_path)
    jobs = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)
    invoices = write_snapshot(tmp_path, "invoices.csv", INVOICES_CSV)

    ingest_snapshot(workspace, jobs, source="jobs", observed_at=FIXED_NOW)
    result = ingest_snapshot(
        workspace, invoices, source="invoices", observed_at=FIXED_NOW
    )

    assert result.ok
    assert "INGEST.UNRESOLVED_REF" not in [
        d.code for d in result.diagnostics
    ]
    for obs in result.observations:
        assert "INGEST.UNRESOLVED_REF" not in codes_of(obs)


def test_refs_resolve_within_the_same_run(tmp_path: Path) -> None:
    # The jobs source references its own Job/Person entities — accepted
    # records in the same run count as known.
    workspace = make_multi(tmp_path)
    jobs = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    result = ingest_snapshot(
        workspace, jobs, source="jobs", observed_at=FIXED_NOW
    )

    assert result.ok
    for obs in result.observations:
        assert "INGEST.UNRESOLVED_REF" not in codes_of(obs)


def test_ref_resolution_is_exact_id_only(tmp_path: Path) -> None:
    # j01 != J01 — references never guess or normalize.
    workspace = make_multi(tmp_path)
    jobs = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)
    invoices = write_snapshot(
        tmp_path,
        "invoices.csv",
        "id,job,issued_at\nINV-1,j01,2026-10-05\n",
    )

    ingest_snapshot(workspace, jobs, source="jobs", observed_at=FIXED_NOW)
    result = ingest_snapshot(
        workspace, invoices, source="invoices", observed_at=FIXED_NOW
    )

    assert result.ok
    unresolved = [
        d
        for d in result.observations[0].diagnostics
        if d.code == "INGEST.UNRESOLVED_REF"
    ]
    assert len(unresolved) == 2


def test_reingest_from_any_source_appends_nothing(tmp_path: Path) -> None:
    workspace = make_multi(tmp_path)
    jobs = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)
    invoices = write_snapshot(tmp_path, "invoices.csv", INVOICES_CSV)

    ingest_snapshot(workspace, jobs, source="jobs", observed_at=FIXED_NOW)
    ingest_snapshot(
        workspace, invoices, source="invoices", observed_at=FIXED_NOW
    )
    second = ingest_snapshot(
        workspace, invoices, source="invoices", observed_at=FIXED_NOW
    )

    assert second.ok
    assert second.canonical is not None
    assert second.canonical.events_appended == 0
    assert second.canonical.events_reobserved == 2


def test_correction_from_a_second_source_keeps_both_facts(
    tmp_path: Path,
) -> None:
    # Correction semantics hold per source: a later invoices snapshot that
    # asserts the same InvoiceIssued at a different occurred_at appends a
    # new fact, links it as possibly_corrects, and warns — nothing is
    # deleted or merged.
    workspace = make_multi(tmp_path)
    invoices = write_snapshot(tmp_path, "invoices.csv", INVOICES_CSV)
    corrected = write_snapshot(
        tmp_path,
        "invoices-later.csv",
        "id,job,issued_at\nINV-1,J01,2026-10-07\nINV-2,J02,2026-10-06\n",
    )

    ingest_snapshot(
        workspace, invoices, source="invoices", observed_at=FIXED_NOW
    )
    result = ingest_snapshot(
        workspace, corrected, source="invoices", observed_at=FIXED_NOW
    )

    assert result.ok
    assert "CANON.POSSIBLE_CORRECTION" in [
        d.code for d in result.diagnostics
    ]
    assert result.canonical is not None
    assert result.canonical.events_appended == 1
    assert any(
        link.relation == "possibly_corrects"
        for link in result.canonical.links
    )


def test_per_source_format_json(tmp_path: Path) -> None:
    # The second source may declare a different format; selection picks
    # the parser, never the filename.
    docs = multi_docs()
    docs["mapping"]["mapping"]["sources"][1]["format"] = "json"
    workspace = make_multi(tmp_path, docs)
    invoices = write_snapshot(
        tmp_path,
        "invoices.csv",  # wrong extension on purpose
        json.dumps(
            [
                {
                    "id": "INV-1",
                    "job": "J01",
                    "issued_at": "2026-10-05",
                }
            ]
        ),
    )

    result = ingest_snapshot(
        workspace, invoices, source="invoices", observed_at=FIXED_NOW
    )

    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.observations[0].entities[0].entity == "Invoice"


# --- source selection at preview ---------------------------------------


def test_preview_requires_source_on_multi_source(tmp_path: Path) -> None:
    workspace = make_multi(tmp_path)

    result = preview_snapshot(
        workspace, JOBS_CSV.encode(), observed_at=FIXED_NOW
    )

    assert not result.ok
    assert result.observations == []
    assert "INGEST.SOURCE_REQUIRED" in [d.code for d in result.diagnostics]


def test_preview_reports_selected_source_and_format(tmp_path: Path) -> None:
    workspace = make_multi(tmp_path)

    result = preview_snapshot(
        workspace,
        INVOICES_CSV.encode(),
        source="invoices",
        observed_at=FIXED_NOW,
    )

    assert result.ok
    assert result.source == "invoices"
    assert result.format == "csv"
    invoice = result.observations[0].entities[0]
    assert invoice.entity == "Invoice"
    # Nothing ingested yet, so no canonical entities — the Job refs warn.
    assert "INGEST.UNRESOLVED_REF" in codes_of(result.observations[0])


def test_preview_ids_differ_across_sources_for_identical_bytes(
    tmp_path: Path,
) -> None:
    # FIND-006: identical sample bytes resolved under different sources
    # produce different facts, so the preview id (and the observation ids
    # derived from it) must carry the selected source — reruns under the
    # same source stay stable.
    workspace = make_multi(tmp_path)
    sample = INVOICES_CSV.encode()

    jobs = preview_snapshot(
        workspace, sample, source="jobs", observed_at=FIXED_NOW
    )
    invoices = preview_snapshot(
        workspace, sample, source="invoices", observed_at=FIXED_NOW
    )
    invoices_again = preview_snapshot(
        workspace, sample, source="invoices", observed_at=FIXED_NOW
    )

    assert jobs.preview_id != invoices.preview_id
    assert jobs.preview_id is not None
    assert jobs.preview_id.endswith("-jobs")
    assert invoices.preview_id.endswith("-invoices")
    job_obs = {o.observation_id for o in jobs.observations}
    inv_obs = {o.observation_id for o in invoices.observations}
    assert job_obs.isdisjoint(inv_obs)
    assert invoices_again.preview_id == invoices.preview_id
    assert {
        o.observation_id for o in invoices_again.observations
    } == inv_obs


def test_preview_matches_ingest_per_source(tmp_path: Path) -> None:
    workspace = make_multi(tmp_path)
    preview = preview_snapshot(
        workspace,
        INVOICES_CSV.encode(),
        source="invoices",
        observed_at=FIXED_NOW,
    )

    scratch = make_multi(tmp_path / "ingest")
    snapshot = write_snapshot(tmp_path / "ingest", "invoices.csv", INVOICES_CSV)
    ingested = ingest_snapshot(
        scratch, snapshot, source="invoices", observed_at=FIXED_NOW
    )

    assert len(preview.observations) == len(ingested.observations) == 2
    for prev, ing in zip(preview.observations, ingested.observations):
        assert prev.status == ing.status
        assert prev.entities == ing.entities
        assert prev.events == ing.events
        assert codes_of(prev) == codes_of(ing)
