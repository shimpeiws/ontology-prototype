"""Workspace persistence: save_workspace (#19).

The save flow validates the complete bundle via the same path as
load_workspace_texts, refuses to write when the base digest no longer
matches disk, activates all four files or none, and never touches
anything outside the four definition files.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest
import yaml
from support import FILES, base_docs, make_workspace

from ontology_core import (
    bundle_digest,
    load_workspace,
    save_workspace,
)


def _texts(workspace: Path) -> dict[str, str]:
    return {
        f"{name}.yaml": (workspace / f"{name}.yaml").read_text(encoding="utf-8")
        for name in FILES
    }


def _listing(workspace: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(workspace.iterdir()) if p.is_file()}


def test_saved_bundle_loads_cleanly_and_matches_draft_digest(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    base = bundle_digest(texts)

    # A draft with an edit, a comment, and CRLF survives byte-exact.
    texts["domain.yaml"] = texts["domain.yaml"].replace(
        "test-domain", "renamed-domain"
    ).replace("\n", "\r\n") + "# saved through the API\n"

    result = save_workspace(workspace, texts, base_digest=base)

    assert result.status == "saved"
    assert result.digest == bundle_digest(texts)
    assert result.workspace is not None
    on_disk = (workspace / "domain.yaml").read_bytes()
    assert on_disk == texts["domain.yaml"].encode("utf-8")

    loaded = load_workspace(workspace)
    assert loaded.ok, [d.format() for d in loaded.diagnostics]
    assert loaded.workspace is not None
    assert loaded.workspace.domain.name == "renamed-domain"


def test_invalid_draft_writes_nothing(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    base = bundle_digest(texts)
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["entity"] = "Jbo"
    texts["mapping.yaml"] = yaml.safe_dump(docs["mapping"], sort_keys=False)
    before = _listing(workspace)

    result = save_workspace(workspace, texts, base_digest=base)

    assert result.status == "invalid"
    assert result.workspace is None
    assert any(
        d.code == "MAPPING.UNKNOWN_ENTITY" for d in result.diagnostics
    )
    assert _listing(workspace) == before


def test_stale_base_digest_conflicts_and_writes_nothing(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    base = bundle_digest(texts)

    # An external (CLI/editor) change lands between open and save.
    domain = workspace / "domain.yaml"
    domain.write_text(domain.read_text() + "\n# external edit\n")

    result = save_workspace(workspace, texts, base_digest=base)

    assert result.status == "conflict"
    assert result.digest == bundle_digest(_texts(workspace))
    assert "# external edit" in domain.read_text()


def test_conflict_retry_with_new_digest_saves(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    stale = bundle_digest(texts)
    (workspace / "agent.yaml").write_text(
        (workspace / "agent.yaml").read_text() + "\n# cli edit\n"
    )

    conflicted = save_workspace(workspace, texts, base_digest=stale)
    assert conflicted.status == "conflict"

    retried = save_workspace(workspace, texts, base_digest=conflicted.digest)
    assert retried.status == "saved"
    assert load_workspace(workspace).ok


def test_file_disappearing_since_base_conflicts(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    base = bundle_digest(texts)
    (workspace / "agent.yaml").unlink()

    result = save_workspace(workspace, texts, base_digest=base)

    assert result.status == "conflict"
    assert not (workspace / "agent.yaml").exists()


def test_failed_commit_rolls_back_all_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    base = bundle_digest(texts)
    texts["domain.yaml"] += "\n# draft\n"
    texts["mapping.yaml"] += "\n# draft\n"
    texts["evaluation.yaml"] += "\n# draft\n"
    before = _listing(workspace)

    import ontology_core.workspace as ws_module

    real_replace = os.replace
    calls = {"n": 0}

    def fail_third_replace(src, dst) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("injected activation failure")
        real_replace(src, dst)

    monkeypatch.setattr(ws_module.os, "replace", fail_third_replace)

    result = save_workspace(workspace, texts, base_digest=base)

    assert result.status == "failed"
    assert "injected activation failure" in (result.message or "")
    assert _listing(workspace) == before
    assert load_workspace(workspace).ok


def test_save_touches_only_workspace_files(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "notes.txt").write_text("unrelated")
    texts = _texts(workspace)

    result = save_workspace(workspace, texts, base_digest=bundle_digest(texts))

    assert result.status == "saved"
    names = {p.name for p in workspace.iterdir()}
    assert names == {
        "domain.yaml",
        "mapping.yaml",
        "evaluation.yaml",
        "agent.yaml",
        "notes.txt",
    }


def test_symlinked_definition_file_refuses_save(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    base = bundle_digest(texts)
    outside = tmp_path / "outside.yaml"
    outside.write_text("schema_version: 1\nkind: domain\ndomain: {}\n")
    (workspace / "domain.yaml").unlink()
    (workspace / "domain.yaml").symlink_to(outside)
    before = _listing(workspace)

    result = save_workspace(workspace, texts, base_digest=base)

    assert result.status == "failed"
    assert "symbolic link" in (result.message or "")
    assert (workspace / "domain.yaml").is_symlink()
    assert _listing(workspace) == before
    assert outside.read_text() == "schema_version: 1\nkind: domain\ndomain: {}\n"


def test_save_to_non_directory_fails(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path / "real")
    texts = _texts(workspace)
    missing = tmp_path / "no-such-dir"

    result = save_workspace(missing, texts, base_digest=bundle_digest(texts))

    assert result.status == "failed"
    assert not missing.exists()


def test_save_preserves_file_modes(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    modes = {
        "domain.yaml": 0o640,
        "mapping.yaml": 0o600,
        "evaluation.yaml": 0o664,
        "agent.yaml": 0o644,
    }
    for name, mode in modes.items():
        (workspace / name).chmod(mode)
    texts = _texts(workspace)

    result = save_workspace(workspace, texts, base_digest=bundle_digest(texts))

    assert result.status == "saved"
    for name, mode in modes.items():
        assert stat.S_IMODE((workspace / name).stat().st_mode) == mode


def test_file_created_by_save_gets_default_mode(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    (workspace / "agent.yaml").unlink()
    # The base digest covers only the files present at open time — the
    # same recipe the API uses when a definition is missing on disk.
    base = bundle_digest({k: v for k, v in texts.items() if k != "agent.yaml"})

    result = save_workspace(workspace, texts, base_digest=base)

    assert result.status == "saved"
    assert stat.S_IMODE((workspace / "agent.yaml").stat().st_mode) == 0o644


def test_external_write_during_activation_conflicts_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cross-process write landing between the digest check and a rename
    stops the save as conflict instead of being silently overwritten."""
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    base = bundle_digest(texts)
    for name in FILES:
        texts[f"{name}.yaml"] += "\n# draft\n"
    before = _listing(workspace)

    import ontology_core.workspace as ws_module

    real_replace = os.replace
    calls = {"n": 0}

    def racing_replace(src, dst) -> None:
        real_replace(src, dst)
        calls["n"] += 1
        if calls["n"] == 1:
            # A CLI/editor write lands mid-commit, before agent.yaml's rename.
            agent = workspace / "agent.yaml"
            agent.write_text(agent.read_text() + "\n# external edit\n")

    monkeypatch.setattr(ws_module.os, "replace", racing_replace)

    result = save_workspace(workspace, texts, base_digest=base)

    assert result.status == "conflict"
    assert "agent.yaml" in (result.message or "")
    # The external edit survived; already-activated files were rolled
    # back to the pre-save bytes; no staging files remain.
    after = _listing(workspace)
    for name in ("domain.yaml", "mapping.yaml", "evaluation.yaml"):
        assert after[name] == before[name]
    assert after["agent.yaml"] == before["agent.yaml"] + b"\n# external edit\n"
    assert set(after) == set(before)
    assert result.digest == bundle_digest(_texts(workspace))
    assert load_workspace(workspace).ok


def test_extra_keys_in_texts_are_never_written(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    texts = _texts(workspace)
    texts["evil.yaml"] = "should: not be written"

    result = save_workspace(workspace, texts, base_digest=bundle_digest(texts))

    assert result.status == "saved"
    assert not (workspace / "evil.yaml").exists()


def test_concurrent_saves_serialize(tmp_path: Path) -> None:
    """Two in-process saves racing one base digest cannot interleave:
    exactly one commits, the other observes the new digest and conflicts."""
    workspace = make_workspace(tmp_path)
    texts_a = _texts(workspace)
    texts_b = dict(texts_a)
    texts_a["domain.yaml"] += "\n# writer A\n"
    texts_b["agent.yaml"] += "\n# writer B\n"
    base = bundle_digest(_texts(workspace))

    results: list = []
    threads = [
        threading.Thread(
            target=lambda t: results.append(
                save_workspace(workspace, t, base_digest=base)
            ),
            args=(texts,),
        )
        for texts in (texts_a, texts_b)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(r.status for r in results) == ["conflict", "saved"]
    assert load_workspace(workspace).ok
