"""Human decisions over persisted action proposals.

Diagnosis persists each *validated* proposal in the append-only
``proposals`` table with its exact payload digest, canonical targets,
related entities, and evidence links. This module adds the lifecycle on
top:

- ``decide`` records an explicit human ``approved``/``rejected`` decision
  bound to the proposal's payload digest, with reviewer identity, time,
  and reason. Decisions are append-only; the latest one wins and every
  past decision stays inspectable.
- ``check_approval`` is the executor-facing gate: a proposal must exist,
  its latest decision must be ``approved``, and the payload presented for
  execution must digest-match what was approved. Unknown, unapproved,
  rejected, and changed payloads all fail closed.

The model never reaches these APIs: decisions only enter through
``decide``, which requires a non-empty reviewer — model output has no
path to masquerade as a human decision. Approval never executes
anything; write-back is a later stage.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import CanonicalStore, SqliteCanonicalStore
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR
from .model import Workspace, param_type_error
from .trace import JsonlTraceSink, TraceSink

DECISIONS = ("approved", "rejected")


# Version tag folded into the payload digest, like the "v1" in
# event_fact_key: if the digest recipe ever changes, stored digests from
# the old recipe can be told apart from new ones.
_DIGEST_VERSION = "v1"


def payload_digest(action: str, params: dict[str, Any]) -> str:
    """Digest of the exact payload a decision binds to.

    Canonical JSON (sorted keys, compact separators) so any param
    reordering, addition, or value change produces a different digest —
    and therefore a failed approval check.
    """
    raw = (
        _DIGEST_VERSION
        + "\n"
        + json.dumps(
            {"action": action, "params": params},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def build_proposal_rows(
    workspace: Workspace,
    run_id: str,
    proposals: list[Any],
    candidates: list[dict[str, Any]],
    created_at: datetime,
) -> list[dict[str, Any]]:
    """Persistable rows for one diagnosis run's validated proposals.

    Each row extracts the canonical target (the ref param pointing at the
    projection subject), every other ref as a related entity, and the
    evidence links of the diagnosed candidate the proposal cites — so an
    FDE can review a proposal with its context, and a decision can bind
    the exact payload digest.

    ``proposal_index`` (and the ``-pN`` id suffix) use the proposal's
    index in the model's raw ``action_proposals`` output, so a persisted
    row always points back at the recorded output it was validated from.
    """
    if not proposals:
        return []
    subject_entity = workspace.domain.projections[
        workspace.agent.projection
    ].subject
    candidate_evidence = {
        c["subject"]["id"]: c.get("evidence", {}) for c in candidates
    }
    rows = []
    for proposal in proposals:
        spec = workspace.agent.permitted_actions[proposal.action]
        subject_id: str | None = None
        related: list[dict[str, str]] = []
        for pname, pspec in spec.params.items():
            value = proposal.params.get(pname)
            if pspec.type != "ref" or not isinstance(value, str):
                continue
            if pspec.ref == subject_entity:
                subject_id = value
            else:
                related.append(
                    {"param": pname, "entity": pspec.ref, "id": value}
                )
        index = proposal.index
        rows.append(
            {
                "proposal_id": f"{run_id}-p{index}",
                "diagnosis_run_id": run_id,
                "proposal_index": index,
                "action": proposal.action,
                "params": proposal.params,
                "reason": proposal.reason,
                "subject_entity": subject_entity,
                "subject_id": subject_id,
                "related_entities": related,
                "evidence": candidate_evidence.get(subject_id, {}),
                "payload_digest": payload_digest(
                    proposal.action, proposal.params
                ),
                "created_at": created_at,
            }
        )
    return rows


@dataclass
class DecisionResult:
    """Outcome of ``decide``: diagnostics plus the recorded decision."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    decision_id: str | None = None
    proposal_id: str | None = None
    decision: str | None = None
    recorded: bool = False

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


def decide(
    workspace: Workspace,
    proposal_id: str,
    decision: str,
    *,
    reviewer: str,
    reason: str | None = None,
    store: CanonicalStore | None = None,
    trace: TraceSink | None = None,
) -> DecisionResult:
    """Record a human decision on a persisted proposal.

    Never raises on bad input: an unknown proposal, an invalid decision
    value, or a missing reviewer all become diagnostics. A decision binds
    the proposal's recorded payload digest — approval of a proposal
    applies to exactly the payload that was reviewed.
    """
    diag = Diagnostics()
    result = DecisionResult(proposal_id=proposal_id)

    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    if trace is None:
        trace = JsonlTraceSink(Path(workspace.path) / RUNTIME_DIR / "trace.jsonl")

    # Every exit goes through _finish so even a rejected *attempt* leaves
    # a decision.failed trace event — a failed approval attempt is itself
    # auditable.
    def _finish() -> DecisionResult:
        result.diagnostics = diag.items
        _emit_trace(trace, result, diag)
        return result

    if decision not in DECISIONS:
        diag.error(
            "DECISION.INVALID_DECISION",
            f"decision must be one of {DECISIONS}, got {decision!r}",
        )
    if not reviewer or not reviewer.strip():
        diag.error(
            "DECISION.NO_REVIEWER",
            "a decision requires an explicit reviewer identity",
        )
    if diag.has_errors:
        return _finish()

    try:
        proposal = store.proposal(proposal_id)
    except Exception as exc:
        diag.error(
            "DECISION.STORE_ERROR",
            f"cannot read canonical store: {exc}",
        )
        proposal = None
    if proposal is None and not diag.has_errors:
        diag.error(
            "DECISION.UNKNOWN_PROPOSAL",
            f"no persisted proposal {proposal_id!r}",
            hint="proposals are recorded when a diagnosis run validates "
            "them; rejected proposals are never persisted",
        )
    if diag.has_errors:
        return _finish()

    decided_at = datetime.now(timezone.utc)
    decision_id = f"dec-{decided_at:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}"
    record = {
        "decision_id": decision_id,
        "proposal_id": proposal_id,
        "decision": decision,
        "reviewer": reviewer.strip(),
        "reason": reason,
        "payload_digest": proposal["payload_digest"],
        "decided_at": decided_at,
    }
    try:
        store.record_decision(record)
        result.recorded = True
        result.decision_id = decision_id
        result.decision = decision
    except Exception as exc:
        diag.error(
            "DECISION.STORE_ERROR",
            f"cannot record decision: {exc}",
        )

    return _finish()


@dataclass
class ApprovalCheck:
    """Executor-facing verdict for one proposal/payload pair."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    proposal_id: str | None = None
    status: str | None = None
    approved: bool = False

    @property
    def ok(self) -> bool:
        return self.approved


def check_approval(
    workspace: Workspace,
    proposal_id: str,
    *,
    action: str,
    params: dict[str, Any],
    store: CanonicalStore | None = None,
) -> ApprovalCheck:
    """Verify a proposal is approved for the exact payload presented.

    Fails closed on every miss: unknown proposal, no decision (status
    ``proposed``), latest decision ``rejected``, a payload whose digest
    differs from the approved one, or an approved payload that no longer
    satisfies the current workspace contract (the action was removed
    from ``permitted_actions`` or its param schema changed).
    ``action``/``params`` are required: the gate binds to the payload
    about to execute, never to whatever happens to be stored.
    """
    diag = Diagnostics()
    result = ApprovalCheck(proposal_id=proposal_id)

    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    try:
        proposal = store.proposal(proposal_id)
        decisions = store.decisions_for(proposal_id)
    except Exception as exc:
        diag.error(
            "DECISION.STORE_ERROR",
            f"cannot read canonical store: {exc}",
        )
        result.diagnostics = diag.items
        return result

    if proposal is None:
        diag.error(
            "DECISION.UNKNOWN_PROPOSAL",
            f"no persisted proposal {proposal_id!r}",
        )
        result.diagnostics = diag.items
        return result

    latest = decisions[-1] if decisions else None
    result.status = latest["decision"] if latest else "proposed"

    if result.status != "approved":
        diag.error(
            "DECISION.NOT_APPROVED",
            f"proposal {proposal_id!r} is {result.status}, not approved",
        )
        result.diagnostics = diag.items
        return result

    expected = latest["payload_digest"]
    presented = payload_digest(action, params)
    if presented != expected or presented != proposal["payload_digest"]:
        diag.error(
            "DECISION.PAYLOAD_CHANGED",
            "the presented payload does not match the approved proposal "
            "digest",
            hint="a changed action or params requires a new decision",
        )
        result.diagnostics = diag.items
        return result

    contract_error = _contract_error(workspace, store, proposal)
    if contract_error is not None:
        diag.error("DECISION.CONTRACT_CHANGED", contract_error)
        result.diagnostics = diag.items
        return result

    result.approved = True
    result.diagnostics = diag.items
    return result


def _contract_error(
    workspace: Workspace, store: CanonicalStore, proposal: dict[str, Any]
) -> str | None:
    """Revalidate the approved payload against the *current* contract.

    An approval binds to a payload, but the workspace contract can change
    between review and execution — the action dropped from
    ``permitted_actions``, a param added or retyped. Executing such a
    proposal would act on an approval the reviewer never gave for the
    current contract, so the gate rechecks: action still allowlisted,
    params still schema-conformant, and ref targets still resolvable.
    Candidate grounding is *not* rechecked — it describes the diagnosis
    context, which is legitimately frozen in time.
    """
    action = proposal["action"]
    params = proposal["params"]
    spec = workspace.agent.permitted_actions.get(action)
    if spec is None:
        return (
            f"action {action!r} is no longer in permitted_actions "
            f"({', '.join(sorted(workspace.agent.permitted_actions))})"
        )
    unknown = sorted(set(params) - set(spec.params))
    if unknown:
        return f"params {unknown} are no longer declared for {action!r}"
    for pname, pspec in spec.params.items():
        value = params.get(pname)
        if value is None:
            if pspec.required:
                return f"param {pname!r} is now required for {action!r}"
            continue
        type_error = param_type_error(value, pspec)
        if type_error is not None:
            return f"param {pname!r}: {type_error}"
        if pspec.type == "ref":
            try:
                entity = store.entity_state(pspec.ref or "", value)
            except Exception as exc:
                return f"cannot resolve ref {pspec.ref}/{value}: {exc}"
            if entity is None:
                return (
                    f"param {pname!r} resolves to unknown "
                    f"{pspec.ref} entity {value!r}"
                )
    return None


def proposal_view(
    workspace: Workspace,
    store: CanonicalStore | None = None,
) -> list[dict[str, Any]]:
    """All proposals with derived lifecycle status and latest decision."""
    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    views = []
    for row in store.proposals():
        decisions = store.decisions_for(row["proposal_id"])
        latest = decisions[-1] if decisions else None
        views.append(
            {
                **row,
                "status": latest["decision"] if latest else "proposed",
                "latest_decision": latest,
                "decisions": decisions,
            }
        )
    return views


def _emit_trace(
    trace: TraceSink, result: DecisionResult, diag: Diagnostics
) -> None:
    try:
        trace.emit(
            "decision",
            "decision.recorded" if result.recorded else "decision.failed",
            {
                "decision_id": result.decision_id,
                "proposal_id": result.proposal_id,
                "decision": result.decision,
                "errors": [
                    d.code for d in diag.items if d.severity == "error"
                ],
            },
        )
    except OSError as exc:
        diag.warning(
            "DECISION.TRACE_ERROR",
            f"cannot write trace: {exc.strerror or exc}",
        )


__all__ = [
    "ApprovalCheck",
    "DECISIONS",
    "DecisionResult",
    "check_approval",
    "decide",
    "payload_digest",
    "proposal_view",
]
