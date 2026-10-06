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

import json
import time
from dataclasses import dataclass
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
    """A spec that never reached evaluation — recorded, not fabricated."""

    provider: str
    model: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "status": "unavailable",
            "reason": self.reason,
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
    could not be evaluated at all.
    """

    eval_version: str
    prompt_version: str
    generated_at: str
    dataset: str
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
    scoring — and its per-run checks are embedded in the report.
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
        started = time.monotonic()
        report = evaluate_prepared(
            prepared,
            adapter,
            model=spec.model,
            repeats=repeats,
            budget=budget,
        )
        latency = time.monotonic() - started
        digests: list[str | None] = []
        run_ids: list[str] = []
        for run in report.runs:
            if run.run_id is None:
                continue
            run_ids.append(run.run_id)
            record = get_diagnosis_run(
                prepared.workspace, run.run_id, store=prepared.store
            )
            digests.append(record.get("input_digest") if record else None)
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
        projection_run_id=prepared.run_id,
        candidates=len(prepared.candidates),
        candidate_ids=[c["subject"]["id"] for c in prepared.candidates],
        input_digest=next(iter(seen)) if len(seen) == 1 else None,
        input_identical=input_identical,
        repeats_requested=repeats,
        budget=budget,
        provider_requests=sum(s.provider_requests for s in subjects),
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
    pass ``None`` to get the in-memory report only.
    """
    if not specs:
        raise EvalError("a comparison needs at least one subject spec")
    prepared = prepare(Path(scratch_dir) / "shared", dataset=dataset)
    report = compare_subjects(
        prepared, specs, resolve, repeats=repeats, budget=budget
    )
    if report_path is not None:
        write_comparison_report(report, report_path)
    return report


def write_comparison_report(
    report: ComparisonReport, path: str | Path
) -> Path:
    """Write the comparison artifact as JSON; never overwrites.

    A comparison report is evidence — silently replacing one would
    destroy the record a previous run produced. Pass a fresh path.
    """
    dest = Path(path)
    if dest.exists():
        raise EvalError(
            f"comparison report {dest} already exists; "
            "pass a fresh artifact path"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return dest
