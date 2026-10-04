from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WORKSPACE = REPO_ROOT / "examples" / "job-invoicing"

FILES = ("domain", "mapping", "evaluation", "agent")


def copy_example(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    for name in FILES:
        shutil.copy(EXAMPLE_WORKSPACE / f"{name}.yaml", workspace / f"{name}.yaml")
    return workspace


def rewrite(workspace: Path, name: str, mutate) -> None:
    path = workspace / f"{name}.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


@pytest.fixture
def example_workspace(tmp_path: Path) -> Path:
    return copy_example(tmp_path)
