"""Dependency-boundary checks: the core never reaches app or framework code."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import ontology_core
import support
import yaml

CORE_SRC = Path(ontology_core.__file__).resolve().parent
EXAMPLES = support.REPO_ROOT / "examples"

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


def _declared_identifiers(workspace_dir: Path) -> set[str]:
    """Every name a workflow declares: the domain itself plus its
    entities, events, projections, mapping sources, metrics, guardrails,
    and permitted action names — the identifiers generality (#59)
    requires the core to know nothing about.

    Property names are deliberately excluded: they are per-row field
    names (``status``, ``name``, ...) too generic to distinguish a
    workflow coupling from ordinary vocabulary. Matching is
    case-sensitive on the declared spelling."""
    identifiers: set[str] = set()

    def doc(name: str) -> dict:
        return yaml.safe_load(
            (workspace_dir / name).read_text(encoding="utf-8")
        )

    domain = doc("domain.yaml")["domain"]
    identifiers.add(domain["name"])
    for key in ("entities", "events", "projections"):
        identifiers.update(domain.get(key) or {})

    mapping = doc("mapping.yaml")["mapping"]
    identifiers.update(s["name"] for s in mapping.get("sources") or ())

    evaluation = doc("evaluation.yaml")["evaluation"]
    identifiers.update(evaluation.get("metrics") or {})
    identifiers.update(
        g["name"] for g in evaluation.get("guardrails") or ()
    )

    agent = doc("agent.yaml")["agent"]
    identifiers.update(agent.get("permitted_actions") or {})
    return identifiers


# The only paths under packages/core/src allowed to mention a declared
# identifier, with the accepted reason for each coupling.
ALLOWED_IDENTIFIERS: dict[str, set[str]] = {
    # G-04 (docs/generality-gaps.md): the CSV reminder adapter is keyed on
    # the SendReminder action name — an accepted v0.3 gap.
    "execution.py": {"SendReminder"},
    # diagnosis_eval.py is the #31 harness over the frozen job-invoicing
    # fixture workspace committed under eval_fixtures/; its scenario text
    # (injection probe) and dataset constants name that fixture's action,
    # domain, and source files.
    "diagnosis_eval.py": {"SendReminder", "job-invoicing", "jobs"},
}

# eval_fixtures/ is the frozen harness input itself (a committed
# job-invoicing workspace plus canned model outputs, issue #31) — data
# the harness replays, not code the workflow's semantics live in.
EXEMPT_DIRS = {"eval_fixtures"}


def test_core_has_no_workflow_identifiers() -> None:
    """v0.3 generality gate (#59): packages/core/src must not contain
    identifiers declared by the example workflows, outside the allowlist."""
    banned: set[str] = set()
    for workspace_dir in sorted(EXAMPLES.iterdir()):
        if (workspace_dir / "domain.yaml").is_file():
            banned |= _declared_identifiers(workspace_dir)
    assert banned, "no example workspaces found — check EXAMPLES path"

    offenders: list[str] = []
    stale: list[str] = []
    scanned: set[str] = set()
    for source in sorted(CORE_SRC.rglob("*.py")):
        if EXEMPT_DIRS & set(
            source.relative_to(CORE_SRC).parts
        ):
            continue
        rel = source.relative_to(CORE_SRC).as_posix()
        scanned.add(rel)
        text = source.read_text(encoding="utf-8")
        found = {
            name
            for name in banned
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text)
        }
        allowed = ALLOWED_IDENTIFIERS.get(rel, set())
        if extra := found - allowed:
            offenders.append(f"{rel}: {sorted(extra)}")
        if missing := allowed - found:
            stale.append(f"{rel}: allowlisted but absent {sorted(missing)}")

    unknown = set(ALLOWED_IDENTIFIERS) - scanned
    assert not unknown, f"allowlist names unscanned files: {sorted(unknown)}"
    assert not stale, f"stale allowlist entries: {stale}"
    assert not offenders, (
        "workflow identifiers in core outside the allowlist: "
        f"{offenders}"
    )
