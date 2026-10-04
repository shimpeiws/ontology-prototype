"""Workspace loading: the four checked definition files, validated together.

A workspace is a directory containing exactly ``domain.yaml``,
``mapping.yaml``, ``evaluation.yaml``, and ``agent.yaml``. Loading is
data-only: no environment interpolation, no external includes, no execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .agentdef import validate_agent
from .diagnostics import Diagnostic, Diagnostics
from .domain import validate_domain
from .evaluation import validate_evaluation
from .mapping import validate_mapping
from .model import Workspace
from .yamlio import load_yaml_file

SUPPORTED_SCHEMA_VERSION = 1

WORKSPACE_FILES = {
    "domain": "domain.yaml",
    "mapping": "mapping.yaml",
    "evaluation": "evaluation.yaml",
    "agent": "agent.yaml",
}


@dataclass
class LoadResult:
    """Outcome of ``load_workspace``: ordered diagnostics plus the typed
    workspace when every file validated without errors."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    workspace: Workspace | None = None

    @property
    def ok(self) -> bool:
        return self.workspace is not None

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]


def load_workspace(path: str | Path) -> LoadResult:
    """Load and validate a workspace directory.

    Never raises on bad input: every problem becomes a diagnostic. A typed
    ``Workspace`` is returned only when all four files validate cleanly.
    """
    diag = Diagnostics()
    root = Path(path)

    if not root.is_dir():
        diag.error(
            "WORKSPACE.NOT_A_DIRECTORY",
            f"workspace path is not a directory: {root}",
        )
        return LoadResult(diagnostics=diag.items)

    docs: dict[str, dict] = {}
    for kind, filename in WORKSPACE_FILES.items():
        file_path = root / filename
        if not file_path.is_file():
            diag.error(
                "WORKSPACE.MISSING_FILE",
                f"missing required definition file {filename}",
                file=filename,
                hint="a workspace needs domain, mapping, evaluation, and agent files",
            )
            continue
        doc = load_yaml_file(file_path, filename, diag)
        if doc is not None and _check_header(doc, kind, filename, diag):
            docs[kind] = doc

    domain = None
    if "domain" in docs:
        domain = validate_domain(docs["domain"], diag, WORKSPACE_FILES["domain"])

    downstream_kinds = [k for k in ("mapping", "evaluation", "agent") if k in docs]
    if domain is None and downstream_kinds:
        diag.warning(
            "WORKSPACE.CROSS_VALIDATION_SKIPPED",
            "domain definition has errors; cross-file reference checks were skipped",
        )

    mapping = (
        validate_mapping(docs["mapping"], diag, WORKSPACE_FILES["mapping"], domain)
        if "mapping" in docs
        else None
    )
    evaluation = (
        validate_evaluation(docs["evaluation"], diag, WORKSPACE_FILES["evaluation"], domain)
        if "evaluation" in docs
        else None
    )
    agent = (
        validate_agent(docs["agent"], diag, WORKSPACE_FILES["agent"], domain)
        if "agent" in docs
        else None
    )

    workspace = None
    if (
        domain is not None
        and mapping is not None
        and evaluation is not None
        and agent is not None
    ):
        workspace = Workspace(
            path=root,
            domain=domain,
            mapping=mapping,
            evaluation=evaluation,
            agent=agent,
        )
    return LoadResult(diagnostics=diag.items, workspace=workspace)


def _check_header(doc: dict, kind: str, file: str, diag: Diagnostics) -> bool:
    """Validate schema_version and kind; deep validation runs only if True."""
    ok = True
    version = doc.get("schema_version")
    if version is None:
        diag.error(
            "SCHEMA.MISSING_FIELD",
            "missing required field 'schema_version'",
            file=file,
            path="schema_version",
        )
        ok = False
    elif isinstance(version, bool) or not isinstance(version, int):
        diag.error(
            "SCHEMA.INVALID_TYPE",
            f"'schema_version' must be an integer, got {version!r}",
            file=file,
            path="schema_version",
        )
        ok = False
    elif version != SUPPORTED_SCHEMA_VERSION:
        diag.error(
            "SCHEMA.VERSION_UNSUPPORTED",
            f"unsupported schema_version {version!r}",
            file=file,
            path="schema_version",
            hint=f"supported schema_version: {SUPPORTED_SCHEMA_VERSION}",
        )
        ok = False

    actual_kind = doc.get("kind")
    if actual_kind is None:
        diag.error(
            "SCHEMA.MISSING_FIELD",
            "missing required field 'kind'",
            file=file,
            path="kind",
            hint=f"expected 'kind: {kind}'",
        )
        ok = False
    elif actual_kind != kind:
        diag.error(
            "SCHEMA.KIND_MISMATCH",
            f"{file} declares kind {actual_kind!r}, expected {kind!r}",
            file=file,
            path="kind",
            hint="check that the file contents match its required name",
        )
        ok = False
    return ok
