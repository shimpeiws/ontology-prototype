from __future__ import annotations

import shutil
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WORKSPACE = REPO_ROOT / "examples" / "job-invoicing"

FILES = ("domain", "mapping", "evaluation", "agent")


def copy_example(root: Path, name: str = "workspace") -> Path:
    workspace = root / name
    workspace.mkdir(parents=True)
    for kind in FILES:
        shutil.copy(EXAMPLE_WORKSPACE / f"{kind}.yaml", workspace / f"{kind}.yaml")
    return workspace


def workspace_texts(workspace: Path) -> dict[str, str]:
    return {
        f"{kind}.yaml": (workspace / f"{kind}.yaml").read_text(encoding="utf-8")
        for kind in FILES
        if (workspace / f"{kind}.yaml").is_file()
    }


def rewrite(workspace: Path, name: str, mutate) -> None:
    path = workspace / f"{name}.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
