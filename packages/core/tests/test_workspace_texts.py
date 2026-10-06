"""Draft-bundle validation: load_workspace_texts and bundle_digest.

The in-memory entry point must agree with load_workspace on the same
content, and must never touch the filesystem.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml
from support import FILES, base_docs, make_workspace

from ontology_core import (
    WORKSPACE_FILES,
    Workspace,
    bundle_digest,
    load_workspace,
    load_workspace_texts,
)


def _texts(workspace: Path) -> dict[str, str]:
    return {
        f"{name}.yaml": (workspace / f"{name}.yaml").read_text(encoding="utf-8")
        for name in FILES
    }


def test_texts_parity_with_disk_valid(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    from_disk = load_workspace(workspace)
    from_texts = load_workspace_texts(_texts(workspace), path=workspace)

    assert from_disk.ok and from_texts.ok
    assert from_texts.diagnostics == from_disk.diagnostics
    assert isinstance(from_texts.workspace, Workspace)
    assert from_texts.workspace.path == workspace
    for attr in ("domain", "mapping", "evaluation", "agent"):
        assert getattr(from_texts.workspace, attr) == getattr(
            from_disk.workspace, attr
        )


def test_texts_parity_with_disk_invalid(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["entity"] = "Jbo"
    docs["evaluation"]["evaluation"]["aggregation"] = "bogus"
    workspace = make_workspace(tmp_path, docs)
    from_disk = load_workspace(workspace)
    from_texts = load_workspace_texts(_texts(workspace), path=workspace)

    assert not from_disk.ok and not from_texts.ok
    assert from_texts.diagnostics == from_disk.diagnostics
    assert from_texts.workspace is None


def test_texts_missing_file_reports_like_disk(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    del texts["domain.yaml"]
    result = load_workspace_texts(texts, path=workspace)
    assert not result.ok
    missing = next(d for d in result.diagnostics if d.code == "WORKSPACE.MISSING_FILE")
    assert missing.file == "domain.yaml"
    assert any(
        d.code == "WORKSPACE.CROSS_VALIDATION_SKIPPED" for d in result.diagnostics
    )


def test_texts_extra_keys_are_ignored(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    texts["notes.yaml"] = "unrelated: true"
    result = load_workspace_texts(texts, path=workspace)
    assert result.ok, [d.format() for d in result.diagnostics]


def test_texts_never_touch_the_filesystem(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    texts["mapping.yaml"] = "not: [valid"
    before = {
        p.name: p.read_bytes() for p in sorted(workspace.iterdir())
    }
    missing_dir = tmp_path / "does-not-exist"
    result = load_workspace_texts(texts, path=missing_dir)
    assert not result.ok
    assert any(d.code == "WORKSPACE.YAML_ERROR" for d in result.diagnostics)
    after = {p.name: p.read_bytes() for p in sorted(workspace.iterdir())}
    assert before == after
    assert not missing_dir.exists()
    assert not (workspace / ".ontology").exists()


def test_texts_size_limit_matches_files(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    texts["domain.yaml"] = "domain: " + "x" * 1_000_001
    result = load_workspace_texts(texts, path=workspace)
    assert not result.ok
    assert any(d.code == "WORKSPACE.FILE_TOO_LARGE" for d in result.diagnostics)


def test_texts_header_checks_apply(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    doc = yaml.safe_load(texts["agent.yaml"])
    doc["kind"] = "domain"
    texts["agent.yaml"] = yaml.safe_dump(doc)
    result = load_workspace_texts(texts, path=workspace)
    assert not result.ok
    assert any(
        d.code == "SCHEMA.KIND_MISMATCH" and d.file == "agent.yaml"
        for d in result.diagnostics
    )


def test_texts_workspace_path_is_the_given_identity(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    virtual = tmp_path / "draft-target"
    result = load_workspace_texts(_texts(workspace), path=virtual)
    assert result.ok
    assert result.workspace is not None
    assert result.workspace.path == virtual


def test_bundle_digest_is_deterministic_and_order_free(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    assert bundle_digest(texts) == bundle_digest(texts)
    assert bundle_digest(texts) == bundle_digest(dict(reversed(list(texts.items()))))


def test_bundle_digest_changes_with_content(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    mutated = copy.deepcopy(texts)
    mutated["domain.yaml"] += "\n# comment\n"
    assert bundle_digest(texts) != bundle_digest(mutated)


def test_bundle_digest_covers_only_known_files(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    extra = dict(texts)
    extra["notes.yaml"] = "unrelated: true"
    assert bundle_digest(extra) == bundle_digest(texts)
    partial = dict(texts)
    del partial["agent.yaml"]
    assert bundle_digest(partial) != bundle_digest(texts)


def test_texts_roundtrip_matches_example(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    # The digest of the on-disk bundle equals the digest of the draft
    # carrying the same content — the comparison #19's save flow relies on.
    assert bundle_digest(texts) == bundle_digest(_texts(workspace))
