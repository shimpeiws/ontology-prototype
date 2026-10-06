"""Workspace loading and saving: the four checked definition files.

A workspace is a directory containing exactly ``domain.yaml``,
``mapping.yaml``, ``evaluation.yaml``, and ``agent.yaml``. Loading is
data-only: no environment interpolation, no external includes, no execution.

Saving activates a draft bundle: whole-bundle validation first, a base
digest check against the on-disk files, then an all-or-nothing activation
(staged temp files, per-file rename, rollback on failure). A save writes
the draft text byte-exact — comments and formatting survive to the extent
the editor preserved them — and never touches anything outside the four
definition files.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .agentdef import validate_agent
from .diagnostics import Diagnostic, Diagnostics
from .domain import validate_domain
from .evaluation import validate_evaluation
from .mapping import validate_mapping
from .model import Workspace
from .yamlio import load_yaml_file, load_yaml_text

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

    try:
        is_dir = root.is_dir()
    except (OSError, ValueError):
        is_dir = False
    if not is_dir:
        diag.error(
            "WORKSPACE.NOT_A_DIRECTORY",
            f"workspace path is not a directory: {root}",
        )
        return LoadResult(diagnostics=diag.items)

    docs: dict[str, dict] = {}
    for kind, filename in WORKSPACE_FILES.items():
        file_path = root / filename
        try:
            exists = file_path.is_file()
        except OSError as exc:
            diag.error(
                "WORKSPACE.UNREADABLE_FILE",
                f"cannot stat file: {exc.strerror or exc}",
                file=filename,
            )
            continue
        if not exists:
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

    return _validate_documents(docs, diag, root)


def load_workspace_texts(
    texts: Mapping[str, str], *, path: str | Path
) -> LoadResult:
    """Validate a workspace bundle supplied as in-memory YAML text.

    ``texts`` maps workspace file names (``domain.yaml``, ``mapping.yaml``,
    ``evaluation.yaml``, ``agent.yaml``) to their UTF-8 text, as a draft
    held by an editor would supply them. A missing key reports the same
    ``WORKSPACE.MISSING_FILE`` diagnostic as a file absent on disk; keys
    outside the four workspace file names are ignored, mirroring how
    ``load_workspace`` ignores extra files in a directory. Nothing is read
    from or written to the filesystem: ``path`` only names the workspace
    directory the bundle belongs to on the returned ``Workspace``.
    """
    diag = Diagnostics()

    docs: dict[str, dict] = {}
    for kind, filename in WORKSPACE_FILES.items():
        text = texts.get(filename)
        if text is None:
            diag.error(
                "WORKSPACE.MISSING_FILE",
                f"missing required definition file {filename}",
                file=filename,
                hint="a workspace needs domain, mapping, evaluation, and agent files",
            )
            continue
        doc = load_yaml_text(text, filename, diag)
        if doc is not None and _check_header(doc, kind, filename, diag):
            docs[kind] = doc

    return _validate_documents(docs, diag, Path(path))


def bundle_digest(texts: Mapping[str, str]) -> str:
    """Content digest of a workspace bundle.

    The digest covers exactly the four workspace file names present in
    ``texts`` and is independent of how the text was obtained (disk read or
    draft payload), so an active bundle and a draft can be compared. The
    recipe is versioned: sha256 over ``ontology-workspace-bundle.v1`` then,
    in ``WORKSPACE_FILES`` order, each present file's name, UTF-8 byte
    length, and content.
    """
    digest = hashlib.sha256()
    digest.update(b"ontology-workspace-bundle.v1\n")
    for filename in WORKSPACE_FILES.values():
        text = texts.get(filename)
        if text is None:
            continue
        data = text.encode("utf-8", errors="surrogatepass")
        digest.update(filename.encode("utf-8"))
        digest.update(b"\n")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\n")
        digest.update(data)
    return digest.hexdigest()


@dataclass
class SaveResult:
    """Outcome of ``save_workspace``.

    ``status`` is one of:

    - ``"saved"`` — the bundle validated, ``base_digest`` matched the
      on-disk bundle, and all four files were activated. ``digest`` is the
      new active digest (equal to ``bundle_digest(texts)``) and
      ``workspace`` holds the validated definitions.
    - ``"invalid"`` — validation failed; nothing was written.
      ``diagnostics`` carries exactly what ``load_workspace_texts``
      reports, so a blocked save explains itself identically to
      ``ontology check``. ``digest`` is the draft digest.
    - ``"conflict"`` — the on-disk bundle no longer matches
      ``base_digest`` (an external or CLI edit, or a file that appeared
      or disappeared since the base was captured); nothing was written.
      ``digest`` is the current on-disk digest — the base a reloaded
      client must present to save.
    - ``"failed"`` — the workspace could not be persisted: not a
      directory, an unreadable or non-regular (e.g. symlinked) definition
      file, or an I/O error during activation. A commit that failed
      partway is rolled back to the pre-save bytes; ``message`` describes
      the failure and says so explicitly when a rollback was incomplete.
    """

    status: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    digest: str | None = None
    workspace: Workspace | None = None
    message: str | None = None


_save_locks_guard = threading.Lock()
_save_locks: dict[str, threading.Lock] = {}


def _save_lock(root: Path) -> threading.Lock:
    """Serialize commit sequences per workspace directory.

    The API serves saves from a threadpool, so two in-process saves of the
    same directory must not interleave their rename/rollback steps.
    Cross-process writers (e.g. the CLI) are covered by the base-digest
    check, not this lock.
    """
    with _save_locks_guard:
        lock = _save_locks.get(str(root))
        if lock is None:
            lock = threading.Lock()
            _save_locks[str(root)] = lock
        return lock


def save_workspace(
    path: str | Path, texts: Mapping[str, str], *, base_digest: str
) -> SaveResult:
    """Validate a draft bundle and activate it as the workspace on disk.

    ``texts`` maps the four workspace file names to their draft YAML text,
    exactly as supplied to ``load_workspace_texts``. ``base_digest`` is the
    digest of the on-disk bundle the draft was based on (the digest an
    open/inspect call returned): the save proceeds only when the on-disk
    bundle still matches it, so an external edit since the base was
    captured produces ``"conflict"`` rather than a silent overwrite.

    The write is all-or-nothing: all four files are staged as synced temp
    files in the workspace directory before the first rename, each target
    is then replaced with ``os.replace``, and a mid-commit failure
    restores every already-activated file to the exact bytes the digest
    check read. Only the four definition files are ever written — saving
    cannot touch canonical state, observations, traces, proposals, or any
    other file in the directory.
    """
    root = Path(path)
    with _save_lock(root):
        return _save_workspace(root, texts, base_digest)


def _save_workspace(
    root: Path, texts: Mapping[str, str], base_digest: str
) -> SaveResult:
    # Whole-bundle validation first — the same authoritative path the
    # CLI's `ontology check` runs, so a saveable draft is exactly a
    # CLI-loadable bundle. An invalid draft never reaches the filesystem.
    result = load_workspace_texts(texts, path=root)
    draft_digest = bundle_digest(texts)
    if not result.ok:
        return SaveResult(
            status="invalid",
            diagnostics=result.diagnostics,
            digest=draft_digest,
        )

    try:
        is_dir = root.is_dir()
    except (OSError, ValueError):
        is_dir = False
    if not is_dir:
        return _save_failed(f"workspace path is not a directory: {root}")

    # Read the on-disk bundle byte-exact — the same recipe the API's open
    # response uses, so a digest captured at open compares equal here.
    # Symlinked definition files are refused outright: activation must
    # never write through a link, and hashing a link target that may live
    # outside the workspace would grant a content-comparison oracle. The
    # pre-save bytes are kept for rollback, so a failed commit restores
    # exactly the state the digest check verified.
    disk_texts: dict[str, str] = {}
    disk_bytes: dict[str, bytes] = {}
    disk_modes: dict[str, int] = {}
    for filename in WORKSPACE_FILES.values():
        file_path = root / filename
        try:
            file_stat = os.lstat(file_path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return _save_failed(
                f"cannot stat {filename}: {exc.strerror or exc}"
            )
        if stat.S_ISLNK(file_stat.st_mode):
            return _save_failed(
                f"{filename} is a symbolic link; refusing to save over it"
            )
        if not stat.S_ISREG(file_stat.st_mode):
            return _save_failed(f"{filename} is not a regular file")
        try:
            data = file_path.read_bytes()
        except OSError as exc:
            return _save_failed(
                f"cannot read {filename}: {exc.strerror or exc}"
            )
        try:
            disk_texts[filename] = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            return _save_failed(f"cannot read {filename}: {exc}")
        disk_bytes[filename] = data
        disk_modes[filename] = stat.S_IMODE(file_stat.st_mode)

    active_digest = bundle_digest(disk_texts)
    if base_digest != active_digest:
        return SaveResult(
            status="conflict",
            digest=active_digest,
            message=(
                "workspace files changed on disk since the base digest was "
                "captured; reload the active bundle before saving"
            ),
        )

    failure = _activate_bundle(root, texts, disk_bytes, disk_modes)
    if failure is not None:
        return _save_failed(failure)
    return SaveResult(
        status="saved",
        diagnostics=result.diagnostics,
        digest=draft_digest,
        workspace=result.workspace,
    )


def _save_failed(message: str) -> SaveResult:
    return SaveResult(status="failed", message=message)


def _activate_bundle(
    root: Path,
    texts: Mapping[str, str],
    disk_bytes: dict[str, bytes],
    disk_modes: dict[str, int],
) -> str | None:
    """Stage then activate the four files; return a failure message or None.

    Commit/rollback strategy: every new file is fully written and synced
    to a temporary name in the workspace directory *before* the first
    rename touches the active set, so a staging failure writes nothing.
    Activation then replaces each target in ``WORKSPACE_FILES`` order
    (``os.replace`` is per-file atomic and replaces — never writes
    through — a symlink). A mid-commit failure restores each
    already-activated file from the pre-save bytes held in memory, so a
    failed save never leaves a mixed old/new bundle.
    """
    staged: dict[str, Path] = {}
    committed: list[str] = []
    try:
        for filename in WORKSPACE_FILES.values():
            data = texts[filename].encode("utf-8")
            fd, tmp = tempfile.mkstemp(
                dir=root, prefix=f".{filename}.", suffix=".new"
            )
            tmp_path = Path(tmp)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                # Replaced files keep their mode; brand-new files get a
                # plain readable mode instead of mkstemp's 0o600.
                os.chmod(tmp_path, disk_modes.get(filename, 0o644))
            except OSError:
                tmp_path.unlink(missing_ok=True)
                raise
            staged[filename] = tmp_path

        for filename in WORKSPACE_FILES.values():
            os.replace(staged[filename], root / filename)
            committed.append(filename)
    except OSError as exc:
        problems = _rollback(root, committed, disk_bytes, disk_modes)
        for tmp_path in staged.values():
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                problems.append(f"leftover staging file {tmp_path.name}")
        message = f"failed to activate workspace: {exc.strerror or exc}"
        if problems:
            message += "; rollback incomplete: " + "; ".join(problems)
        return message

    _fsync_dir(root)
    return None


def _rollback(
    root: Path,
    committed: list[str],
    disk_bytes: dict[str, bytes],
    disk_modes: dict[str, int],
) -> list[str]:
    """Restore files activated before a commit failure.

    Returns a list of per-file problems; an empty list means every
    committed file is back to its pre-save bytes. Files the commit never
    reached were never touched, and files absent before the save are
    removed rather than restored.
    """
    problems: list[str] = []
    for filename in reversed(committed):
        target = root / filename
        original = disk_bytes.get(filename)
        try:
            if original is None:
                target.unlink(missing_ok=True)
            else:
                fd, tmp = tempfile.mkstemp(
                    dir=root, prefix=f".{filename}.", suffix=".restore"
                )
                tmp_path = Path(tmp)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(original)
                    os.chmod(tmp_path, disk_modes.get(filename, 0o644))
                    os.replace(tmp_path, target)
                except OSError:
                    tmp_path.unlink(missing_ok=True)
                    raise
        except OSError as exc:
            problems.append(f"{filename}: {exc.strerror or exc}")
    return problems


def _fsync_dir(root: Path) -> None:
    """Best-effort durability for the renamed directory entries."""
    try:
        fd = os.open(root, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _validate_documents(
    docs: dict[str, dict], diag: Diagnostics, root: Path
) -> LoadResult:
    domain = None
    if "domain" in docs:
        domain = validate_domain(docs["domain"], diag, WORKSPACE_FILES["domain"])

    downstream_kinds = [k for k in ("mapping", "evaluation", "agent") if k in docs]
    if domain is None and downstream_kinds:
        diag.warning(
            "WORKSPACE.CROSS_VALIDATION_SKIPPED",
            "domain.yaml is missing or invalid; cross-file reference checks "
            "were skipped",
            file=WORKSPACE_FILES["domain"],
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
