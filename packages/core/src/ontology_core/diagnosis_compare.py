"""Shared-input provider/model comparison runner (issue #35).

Where ``diagnosis_eval`` exercises the diagnosis pipeline with
controlled doubles, this module runs the *comparison* a live-model
evaluation needs: a list of provider/model subjects scored against
**one** shared :class:`PreparedEval` — identical candidates, evidence
ids, projection run id, and input digest — and a single JSON report
artifact that a human executor then turns into the committed
comparison report (``docs/diagnosis-comparison-report.md``).

- ``compare_subjects`` evaluates subjects against a caller-prepared
  ``PreparedEval`` through ``evaluate_prepared``. It never prepares a
  workspace itself, so identical input is structural, not assumed.
- ``run_comparison`` is the one-call entry point: prepare once,
  compare, optionally write the artifact.
- ``write_comparison_report`` writes the JSON artifact and refuses to
  overwrite — a comparison report is evidence, not scratch output.

Adapters are resolved through a caller-supplied callable (the CLI
passes ``resolve_provider``); the core never imports app adapters. A
subject whose provider cannot be resolved — missing credential,
unknown name — lands in the report's explicit ``unavailable`` slot:
recorded, never fabricated.
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .diagnosis import (
    PROMPT_VERSION,
    DiagnosisProvider,
    ProviderError,
    get_diagnosis_run,
)
from .diagnosis_eval import (
    DATASET_CLEAN,
    DEFAULT_BUDGET,
    EVAL_VERSION,
    CountingProvider,
    EvalError,
    PreparedEval,
    RunBudget,
    SubjectReport,
    evaluate_prepared,
    prepare,
)

# The status a resolved subject gets when its budget allowed zero runs.
STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class SubjectSpec:
    """One subject of a comparison: a provider identity and a model id.

    ``provider`` is the name handed to the caller's resolver (e.g. the
    CLI's ``resolve_provider``); ``model`` is the model id recorded on
    every diagnosis run the subject produces.
    """

    provider: str
    model: str


@dataclass
class UnavailableSubject:
    """A spec that never produced a comparable result — recorded, not
    fabricated.

    ``provider_requests``/``run_ids`` carry whatever a mid-evaluation
    fault had already consumed and persisted, so the report never
    under-counts live calls or loses run evidence.
    """

    provider: str
    model: str
    reason: str
    provider_requests: int = 0
    run_ids: list[str] = field(default_factory=list)
    latency_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "status": "unavailable",
            "reason": self.reason,
            "provider_requests": self.provider_requests,
            "run_ids": self.run_ids,
            "latency_seconds": self.latency_seconds,
        }


@dataclass
class ComparedSubject:
    """One evaluated subject's row in the comparison report.

    ``status`` is the aggregate run outcome: ``ok`` when every
    completed run succeeded, ``failed`` when any failed, ``skipped``
    when the budget allowed zero runs. ``input_digest`` is the digest
    recorded on the subject's run records — ``None`` when no run was
    recorded or the subject's own runs disagree, which also forces the
    report's ``input_identical`` flag off.
    """

    provider: str
    model: str
    status: str
    passed: bool
    provider_requests: int
    latency_seconds: float
    repeats_requested: int
    repeats_completed: int
    repeats_clamped: bool
    within_budget: bool
    projection_run_id: str | None
    input_digest: str | None
    run_ids: list[str]
    runs: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "passed": self.passed,
            "provider_requests": self.provider_requests,
            "latency_seconds": self.latency_seconds,
            "repeats_requested": self.repeats_requested,
            "repeats_completed": self.repeats_completed,
            "repeats_clamped": self.repeats_clamped,
            "within_budget": self.within_budget,
            "projection_run_id": self.projection_run_id,
            "input_digest": self.input_digest,
            "run_ids": self.run_ids,
            "runs": self.runs,
        }


@dataclass
class ComparisonReport:
    """Outcome of one comparison: the shared input plus every subject.

    ``input_digest``/``input_identical`` are the artifact's proof that
    all evaluated subjects saw identical input: the flag is true only
    when every resolved subject has a non-null digest and they all
    agree. ``unavailable`` is the explicit slot for candidates that
    could not be evaluated at all; requests a faulted attempt already
    burned still fold into ``provider_requests``.
    """

    eval_version: str
    prompt_version: str
    generated_at: str
    dataset: str
    workspace_dir: str
    projection_run_id: str
    candidates: int
    candidate_ids: list[str]
    input_digest: str | None
    input_identical: bool
    repeats_requested: int
    budget: RunBudget
    provider_requests: int
    subjects: list[ComparedSubject]
    unavailable: list[UnavailableSubject]

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_version": self.eval_version,
            "prompt_version": self.prompt_version,
            "generated_at": self.generated_at,
            "dataset": self.dataset,
            "workspace_dir": self.workspace_dir,
            "projection_run_id": self.projection_run_id,
            "candidates": self.candidates,
            "candidate_ids": self.candidate_ids,
            "input_digest": self.input_digest,
            "input_identical": self.input_identical,
            "repeats_requested": self.repeats_requested,
            "budget": {
                "repeats": self.budget.repeats,
                "max_provider_requests": self.budget.max_provider_requests,
            },
            "provider_requests": self.provider_requests,
            "subjects": [s.to_dict() for s in self.subjects],
            "unavailable": [u.to_dict() for u in self.unavailable],
        }


def _subject_status(report: SubjectReport) -> str:
    if not report.runs:
        return STATUS_SKIPPED
    if all(run.status == "ok" for run in report.runs):
        return "ok"
    return "failed"


def _persisted_run_ids(prepared: PreparedEval) -> list[str]:
    """Run ids the shared store has recorded — best effort, since the
    store itself may be what faulted."""
    try:
        return [
            row["run_id"] for row in prepared.store.diagnosis_runs()
        ]
    except Exception:
        return []


def _fault_run_ids(
    collected: list[str], prepared: PreparedEval, known: set[str]
) -> list[str]:
    """Run ids a faulted subject produced: the ones already collected
    from its report plus any new records in the shared store."""
    ids = list(collected)
    ids.extend(
        run_id
        for run_id in _persisted_run_ids(prepared)
        if run_id not in known and run_id not in ids
    )
    return ids


def compare_subjects(
    prepared: PreparedEval,
    specs: Sequence[SubjectSpec],
    resolve: Callable[[str], DiagnosisProvider],
    *,
    repeats: int = DEFAULT_BUDGET.repeats,
    budget: RunBudget = DEFAULT_BUDGET,
) -> ComparisonReport:
    """Evaluate every subject against one shared ``PreparedEval``.

    ``resolve`` maps a provider name to an adapter; resolution failure
    records the spec in ``unavailable`` rather than raising, so the
    artifact shows exactly which candidates produced evidence. Every
    evaluated subject goes through ``evaluate_prepared`` — identical
    candidates, identical input digest, identical deterministic
    scoring — and its per-run checks are embedded in the report. A
    subject whose evaluation faults mid-run still contributes the
    provider requests it consumed and the run ids it recorded to the
    artifact — a live call is never under-counted.
    """
    subjects: list[ComparedSubject] = []
    unavailable: list[UnavailableSubject] = []
    for spec in specs:
        try:
            adapter = resolve(spec.provider)
        except ProviderError as exc:
            # ProviderError messages are sanitized by construction —
            # adapters must not put credentials or request detail in them.
            unavailable.append(
                UnavailableSubject(spec.provider, spec.model, str(exc))
            )
            continue
        except Exception as exc:
            # Same rule as the diagnosis pipeline: an unexpected error's
            # message may embed credentials, so only the class is recorded.
            unavailable.append(
                UnavailableSubject(
                    spec.provider,
                    spec.model,
                    f"unexpected {type(exc).__name__}",
                )
            )
            continue
        # The counter is owned here, not inside evaluate_prepared, so a
        # mid-evaluation fault cannot take the subject's request count
        # down with it.
        counting = CountingProvider(adapter)
        known_run_ids = set(_persisted_run_ids(prepared))
        started = time.monotonic()
        digests: list[str | None] = []
        run_ids: list[str] = []
        try:
            report = evaluate_prepared(
                prepared,
                counting,
                model=spec.model,
                repeats=repeats,
                budget=budget,
            )
            for run in report.runs:
                if run.run_id is None:
                    continue
                run_ids.append(run.run_id)
                record = get_diagnosis_run(
                    prepared.workspace, run.run_id, store=prepared.store
                )
                digests.append(
                    record.get("input_digest") if record else None
                )
        except (ProviderError, EvalError) as exc:
            # Trusted, sanitized messages (same rule as resolution).
            unavailable.append(
                UnavailableSubject(
                    spec.provider,
                    spec.model,
                    f"evaluation failed: {exc}",
                    provider_requests=len(counting.requests),
                    run_ids=_fault_run_ids(
                        run_ids, prepared, known_run_ids
                    ),
                    latency_seconds=round(
                        time.monotonic() - started, 3
                    ),
                )
            )
            continue
        except Exception as exc:
            # One subject's unexpected failure must not lose the evidence
            # other subjects already produced; only the class is recorded.
            unavailable.append(
                UnavailableSubject(
                    spec.provider,
                    spec.model,
                    f"evaluation failed: unexpected {type(exc).__name__}",
                    provider_requests=len(counting.requests),
                    run_ids=_fault_run_ids(
                        run_ids, prepared, known_run_ids
                    ),
                    latency_seconds=round(
                        time.monotonic() - started, 3
                    ),
                )
            )
            continue
        latency = time.monotonic() - started
        shared = (
            digests[0]
            if digests and all(d == digests[0] for d in digests)
            else None
        )
        subjects.append(
            ComparedSubject(
                provider=spec.provider,
                model=spec.model,
                status=_subject_status(report),
                passed=report.passed,
                provider_requests=report.provider_requests,
                latency_seconds=round(latency, 3),
                repeats_requested=report.repeats_requested,
                repeats_completed=report.repeats_completed,
                repeats_clamped=report.repeats_clamped,
                within_budget=report.within_budget,
                projection_run_id=prepared.run_id,
                input_digest=shared,
                run_ids=run_ids,
                runs=[r.to_dict() for r in report.runs],
            )
        )
    seen = {s.input_digest for s in subjects if s.input_digest is not None}
    input_identical = (
        bool(subjects)
        and len(seen) == 1
        and all(
            s.input_digest is not None
            and s.projection_run_id == prepared.run_id
            for s in subjects
        )
    )
    return ComparisonReport(
        eval_version=EVAL_VERSION,
        prompt_version=PROMPT_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        dataset=prepared.dataset,
        workspace_dir=str(prepared.workspace_dir),
        projection_run_id=prepared.run_id,
        candidates=len(prepared.candidates),
        candidate_ids=[c["subject"]["id"] for c in prepared.candidates],
        input_digest=next(iter(seen)) if len(seen) == 1 else None,
        input_identical=input_identical,
        repeats_requested=repeats,
        budget=budget,
        provider_requests=(
            sum(s.provider_requests for s in subjects)
            + sum(u.provider_requests for u in unavailable)
        ),
        subjects=subjects,
        unavailable=unavailable,
    )


def run_comparison(
    scratch_dir: str | Path,
    specs: Sequence[SubjectSpec],
    resolve: Callable[[str], DiagnosisProvider],
    *,
    report_path: str | Path | None = None,
    repeats: int = DEFAULT_BUDGET.repeats,
    budget: RunBudget = DEFAULT_BUDGET,
    dataset: str = DATASET_CLEAN,
) -> ComparisonReport:
    """Prepare one frozen input, evaluate every subject, write the artifact.

    The single ``prepare`` call means identical input across subjects is
    structural — this runner cannot re-materialize a workspace per
    subject even by accident. ``report_path`` writes the JSON artifact;
    pass ``None`` to get the in-memory report only. An existing
    ``report_path`` is rejected up front — before preparation or any
    provider call — since live requests cannot be un-spent when the
    write would fail anyway.
    """
    if not specs:
        raise EvalError("a comparison needs at least one subject spec")
    if report_path is not None and os.path.lexists(report_path):
        raise _existing_report_error(Path(report_path))
    prepared = prepare(Path(scratch_dir) / "shared", dataset=dataset)
    report = compare_subjects(
        prepared, specs, resolve, repeats=repeats, budget=budget
    )
    if report_path is not None:
        write_comparison_report(report, report_path)
    return report


def _existing_report_error(dest: Path) -> EvalError:
    return EvalError(
        f"comparison report {dest} already exists; "
        "pass a fresh artifact path"
    )


# Errors that mean "this filesystem cannot hard-link", as opposed to a
# real failure like ENOSPC.
_LINK_UNSUPPORTED = frozenset(
    {errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV}
)


def _created_mode() -> int:
    """The permission bits a plain create gets: 0o666 & ~umask."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def write_comparison_report(
    report: ComparisonReport, path: str | Path
) -> Path:
    """Write the comparison artifact as JSON; never overwrites.

    A comparison report is evidence — silently replacing one would
    destroy the record a previous run produced. The payload is
    serialized first and written to a temporary file in the
    destination directory, then published with a hard link: an
    interrupted write can strand only a cleaned-up temp file, never a
    partial report at the final path, and a concurrent write to the
    same path still fails instead of racing a check-then-create. A
    filesystem that cannot hard-link has no atomic no-overwrite
    publish, so the write fails cleanly — nothing is created at the
    destination — rather than risking a stranded partial artifact.
    Pass a fresh path.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.to_dict(), indent=2) + "\n"
    # A fixed short prefix keeps the temp basename within NAME_MAX even
    # when ``dest.name`` is near the limit.
    fd, tmp_name = tempfile.mkstemp(
        dir=dest.parent, prefix=".compare-", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        # mkstemp creates the file 0600; a report is meant to be read
        # by collaborators, so publish it with the mode a normal create
        # would get under the umask.
        os.chmod(tmp, _created_mode())
        try:
            os.link(tmp, dest)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise _existing_report_error(dest) from None
            if exc.errno in _LINK_UNSUPPORTED:
                raise EvalError(
                    f"cannot publish comparison report {dest}: this "
                    "filesystem does not support atomic no-overwrite "
                    "writes (hard links); pass an artifact path on a "
                    "filesystem that does"
                ) from None
            raise
    finally:
        tmp.unlink(missing_ok=True)
    return dest
