"""Dependency-boundary checks: the core never reaches app or framework code."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import ontology_core

CORE_SRC = Path(ontology_core.__file__).resolve().parent

FORBIDDEN_IMPORT_ROOTS = {
    "ontology_cli",
    "fastapi",
    "flask",
    "django",
    "starlette",
    "uvicorn",
}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_import_purity() -> None:
    offenders = []
    for source in CORE_SRC.rglob("*.py"):
        bad = _imported_roots(source) & FORBIDDEN_IMPORT_ROOTS
        if bad:
            offenders.append(f"{source.name}: {sorted(bad)}")
    assert offenders == [], f"forbidden imports in core: {offenders}"


def test_core_loads_without_app_or_framework_modules() -> None:
    # Run in a clean interpreter: importing ontology_core must not pull in
    # app or server/frontend modules.
    script = (
        "import sys, ontology_core; "
        f"bad = set(sys.modules) & {set(FORBIDDEN_IMPORT_ROOTS)!r}; "
        "assert not bad, bad"
    )
    import subprocess

    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr


def test_core_runtime_dependencies() -> None:
    # Only PyYAML is a runtime dependency; nothing else may appear.
    import importlib.metadata

    requires = importlib.metadata.requires("ontology-core") or []
    runtime = [r for r in requires if "extra" not in r]
    assert len(runtime) == 1
    assert runtime[0].lower().startswith("pyyaml")
