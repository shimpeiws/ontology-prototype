"""Workspace file loading: IO failures, YAML safety, and headers."""

from __future__ import annotations

from pathlib import Path

from support import base_docs, codes, make_workspace

from ontology_core import load_workspace


def test_not_a_directory(tmp_path: Path) -> None:
    result = load_workspace(tmp_path / "does-not-exist")
    assert not result.ok
    assert "WORKSPACE.NOT_A_DIRECTORY" in codes(result)


def test_missing_file(tmp_path: Path) -> None:
    docs = base_docs()
    workspace = make_workspace(tmp_path, docs)
    (workspace / "agent.yaml").unlink()
    result = load_workspace(workspace)
    assert not result.ok
    assert "WORKSPACE.MISSING_FILE" in codes(result)


def test_empty_file(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"] = ""
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "WORKSPACE.EMPTY_FILE" in codes(result)


def test_non_mapping_document(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"] = "- just\n- a\n- list\n"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.INVALID_DOCUMENT" in codes(result)


def test_yaml_syntax_error(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"] = "schema_version: 1\nkind: mapping\n  bad indent: ["
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "WORKSPACE.YAML_ERROR" in codes(result)


def test_unsafe_yaml_tag_rejected(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"] = (
        "schema_version: 1\nkind: domain\n"
        "domain: !!python/object/apply:os.system ['echo pwned']\n"
    )
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "WORKSPACE.YAML_ERROR" in codes(result)


def test_duplicate_key_diagnostic(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"] = (
        "schema_version: 1\nkind: domain\n"
        "domain:\n  name: first\n  name: second\n"
    )
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "WORKSPACE.DUPLICATE_KEY" in codes(result)
    dup = next(d for d in result.diagnostics if d.code == "WORKSPACE.DUPLICATE_KEY")
    assert dup.line is not None


def test_schema_version_missing(tmp_path: Path) -> None:
    docs = base_docs()
    del docs["domain"]["schema_version"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.MISSING_FIELD" in codes(result)


def test_schema_version_wrong_type(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["schema_version"] = "one"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.INVALID_TYPE" in codes(result)


def test_schema_version_unsupported(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["schema_version"] = 99
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.VERSION_UNSUPPORTED" in codes(result)


def test_header_failure_skips_deep_validation(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["schema_version"] = 99
    docs["domain"]["domain"]["entities"] = "not-a-map"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert "SCHEMA.VERSION_UNSUPPORTED" in codes(result)
    assert "SCHEMA.INVALID_TYPE" not in codes(result)


def test_kind_mismatch(tmp_path: Path) -> None:
    docs = base_docs()
    docs["agent"]["kind"] = "domain"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.KIND_MISMATCH" in codes(result)


def test_non_utf8_file_becomes_diagnostic(tmp_path: Path) -> None:
    docs = base_docs()
    workspace = make_workspace(tmp_path, docs)
    (workspace / "domain.yaml").write_bytes(b"schema_version: 1\nname: caf\xe9\n")
    result = load_workspace(workspace)
    assert not result.ok
    assert "WORKSPACE.ENCODING_ERROR" in codes(result)


def test_embedded_null_path_becomes_diagnostic() -> None:
    result = load_workspace("bad\0path")
    assert not result.ok
    assert "WORKSPACE.NOT_A_DIRECTORY" in codes(result)


def test_deeply_nested_yaml_becomes_diagnostic(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"] = "x: " + "[" * 5000 + "]" * 5000
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "WORKSPACE.YAML_ERROR" in codes(result)


def test_oversized_file_rejected(tmp_path: Path) -> None:
    docs = base_docs()
    workspace = make_workspace(tmp_path, docs)
    (workspace / "agent.yaml").write_bytes(b"# " + b"x" * 1_100_000)
    result = load_workspace(workspace)
    assert not result.ok
    assert "WORKSPACE.FILE_TOO_LARGE" in codes(result)


def test_merge_key_override_is_duplicate(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"] = (
        "schema_version: 1\nkind: domain\n"
        "domain:\n  name: &n first\n  <<: {name: second}\n"
    )
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "WORKSPACE.DUPLICATE_KEY" in codes(result)


def test_yaml_error_message_has_no_file_body(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"] = 'schema_version: 1\nkind: domain\nsecret_line: "BODY-SENTINEL"\nbad: ['
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    yaml_errors = [d for d in result.diagnostics if d.code == "WORKSPACE.YAML_ERROR"]
    assert yaml_errors
    for diagnostic in yaml_errors:
        assert "BODY-SENTINEL" not in diagnostic.message


def test_no_env_interpolation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ONTOLOGY_TEST_SENTINEL", "expanded-value")
    docs = base_docs()
    docs["domain"]["domain"]["name"] = "$ONTOLOGY_TEST_SENTINEL"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert result.ok
    assert result.workspace is not None
    assert result.workspace.domain.name == "$ONTOLOGY_TEST_SENTINEL"


def test_diagnostics_never_raise(tmp_path: Path) -> None:
    # A workspace where every file is broken still returns diagnostics.
    docs = {name: "not: [valid" for name in ("domain", "mapping", "evaluation", "agent")}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert len(result.errors) == 4
