"""Model-backed diagnosis over frozen projection candidates.

``run_diagnosis`` hands a deterministic input — the frozen candidate set,
the agent task's goal/deliverables, and the allowlisted action schemas —
to a pluggable ``DiagnosisProvider`` and validates the structured output
back in the core. Provider SDK details live in adapters (the CLI ships an
OpenRouter adapter); the interface crosses the boundary as plain text:

- providers receive a rendered prompt plus an explicit model id and return
  the model's raw text;
- the core parses that text as the diagnosis contract, validates each
  deliverable and every action proposal against ``permitted_actions``,
  and splits proposals into validated and rejected.

Malformed output, disallowed actions, invalid params, and provider
failures never produce an executable proposal: they become diagnostics
and ``rejected`` entries. Every run — including failed ones — is recorded
append-only in ``diagnosis_runs`` with the provider/model identity, prompt
version, candidate input digest, raw output, and validation result, so a
run is inspectable after the fact. Diagnosis reads canonical state but
never mutates it, and it neither approves nor executes proposals — those
are separate stages owned by later issues.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .canonical import CanonicalStore, SqliteCanonicalStore
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR
from .model import PropertySpec, Workspace
from .projections import run_projection
from .trace import JsonlTraceSink, TraceSink

# Version of the diagnosis prompt/task contract. Bump when the prompt
# shape or the output contract changes so recorded runs stay comparable.
PROMPT_VERSION = "diagnosis.v1"


class ProviderError(Exception):
    """A provider call failed in a way adapters can report safely.

    Adapters must build the message from sanitized details only — never
    include credentials, request headers, or raw response bodies that
    could carry them.
    """


@dataclass(frozen=True)
class ProviderRequest:
    """The transport-independent request handed to a provider adapter."""

    prompt: str
    model: str


class DiagnosisProvider(Protocol):
    """Boundary between the core and one AI provider.

    An adapter implements ``complete`` and exposes its stable identity via
    ``name``. Provider SDK objects never appear here: the request and the
    response are plain strings.
    """

    @property
    def name(self) -> str: ...

    def complete(self, request: ProviderRequest) -> str:
        """Return the model's raw text for ``request``.

        Implementations raise ``ProviderError`` (or let ``run_diagnosis``
        convert any other exception) on failure; they never return
        partially-parsed structures.
        """
        ...


@dataclass(frozen=True)
class ActionProposal:
    """One validated action proposal — never executed at this stage."""

    action: str
    params: dict[str, Any]
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "params": self.params, "reason": self.reason}


@dataclass
class DiagnosisResult:
    """Outcome of ``run_diagnosis``: diagnostics, run identity, the
    validated deliverables, and the accepted/rejected proposal split."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    run_id: str | None = None
    projection_run_id: str | None = None
    provider: str | None = None
    model: str | None = None
    status: str | None = None
    candidate_count: int = 0
    deliverables: dict[str, Any] = field(default_factory=dict)
    proposals: list[ActionProposal] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    recorded: bool = False

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


def run_diagnosis(
    workspace: Workspace,
    provider: DiagnosisProvider,
    *,
    model: str,
    run_id: str | None = None,
    as_of: datetime | None = None,
    store: CanonicalStore | None = None,
    trace: TraceSink | None = None,
) -> DiagnosisResult:
    """Diagnose projection candidates through ``provider`` and record the run.

    Exactly one candidate source is used: ``run_id`` replays a frozen
    projection run's candidates; ``as_of`` computes a fresh projection
    first. Never raises on bad input, provider failure, or malformed model
    output — everything becomes a diagnostic.
    """
    diag = Diagnostics()
    result = DiagnosisResult(provider=provider.name, model=model)

    if run_id is not None and as_of is not None:
        diag.error(
            "DIAGNOSIS.CONFLICTING_ARGS",
            "a diagnosis run takes either a frozen projection run id or "
            "an as_of time, not both",
        )
    elif run_id is None and as_of is None:
        diag.error(
            "DIAGNOSIS.NO_INPUT",
            "no candidate source: pass a frozen projection run id or an "
            "as_of time",
        )
    if not model:
        diag.error(
            "DIAGNOSIS.NO_MODEL",
            "no model configured",
            hint="choose the model through explicit configuration "
            "(e.g. the CLI --model option or its documented default)",
        )
    if diag.has_errors:
        result.diagnostics = diag.items
        return result

    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    if trace is None:
        trace = JsonlTraceSink(Path(workspace.path) / RUNTIME_DIR / "trace.jsonl")

    candidates: list[dict[str, Any]] = []
    if run_id is not None:
        try:
            record = store.projection_run(run_id)
        except Exception as exc:
            diag.error(
                "DIAGNOSIS.STORE_ERROR",
                f"cannot read canonical store: {exc}",
            )
            record = None
        if record is None and not diag.has_errors:
            diag.error(
                "DIAGNOSIS.UNKNOWN_RUN",
                f"no frozen projection run {run_id!r}",
                hint="runs are recorded only when the projection evaluates "
                "without errors",
            )
        if record is not None:
            if record["projection"] != workspace.agent.projection:
                diag.error(
                    "DIAGNOSIS.RUN_MISMATCH",
                    f"run {run_id!r} is a {record['projection']!r} run; the "
                    f"workspace agent tasks "
                    f"{workspace.agent.projection!r}",
                )
            else:
                candidates = record["candidates"]
                result.projection_run_id = run_id
    else:
        assert as_of is not None
        projection = run_projection(
            workspace,
            workspace.agent.projection,
            as_of=as_of,
            store=store,
            trace=trace,
        )
        if projection.ok:
            result.projection_run_id = projection.run_id
            candidates = [c.to_dict() for c in projection.candidates]
        else:
            for item in projection.diagnostics:
                if item.severity == "error":
                    diag.error(
                        item.code,
                        item.message,
                        file=item.file,
                        path=item.path,
                        hint=item.hint,
                    )
                else:
                    diag.warning(item.code, item.message)

    if diag.has_errors:
        result.diagnostics = diag.items
        _emit_trace(trace, result, diag)
        return result

    result.candidate_count = len(candidates)
    prompt = build_prompt(workspace, candidates)
    input_digest = _input_digest(workspace, result.projection_run_id, candidates)
    created_at = datetime.now(timezone.utc)
    result.run_id = (
        f"dgx-{created_at:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}"
    )

    raw_output: str | None = None
    provider_called = False
    if candidates:
        provider_called = True
        try:
            raw_output = provider.complete(
                ProviderRequest(prompt=prompt, model=model)
            )
        except Exception as exc:
            detail = exc if isinstance(exc, ProviderError) else ProviderError(str(exc))
            diag.error(
                "DIAGNOSIS.PROVIDER_ERROR",
                f"provider call failed: {detail}",
            )
    else:
        diag.warning(
            "DIAGNOSIS.NO_CANDIDATES",
            "no candidates in scope; the provider was not called",
        )

    deliverables: dict[str, Any] = {}
    if provider_called and not diag.has_errors:
        deliverables = _validate_output(
            workspace, raw_output or "", candidates, store, diag, result
        )
        result.deliverables = deliverables

    result.status = "failed" if diag.has_errors else "ok"

    try:
        store.record_diagnosis_run(
            {
                "run_id": result.run_id,
                "projection_run_id": result.projection_run_id,
                "provider": provider.name,
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "prompt": prompt,
                "status": result.status,
                "input_digest": input_digest,
                "output": raw_output,
                "provider_called": provider_called,
                "deliverables": deliverables,
                "proposals": [p.to_dict() for p in result.proposals],
                "rejected": result.rejected,
                "diagnostics": [d.to_dict() for d in diag.items],
                "created_at": created_at,
            }
        )
        result.recorded = True
    except Exception as exc:
        diag.error(
            "DIAGNOSIS.STORE_ERROR",
            f"cannot record diagnosis run: {exc}",
        )
        result.status = "failed"

    _emit_trace(trace, result, diag)
    result.diagnostics = diag.items
    return result


def get_diagnosis_run(
    workspace: Workspace, run_id: str, *, store: CanonicalStore | None = None
) -> dict[str, Any] | None:
    """Read back a recorded diagnosis run by id."""
    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    return store.diagnosis_run(run_id)


def build_prompt(workspace: Workspace, candidates: list[dict[str, Any]]) -> str:
    """Render the diagnosis prompt deterministically from the workspace
    contract and the frozen candidate set.

    Candidate properties and evidence are embedded as data inside a JSON
    block — adversarial source text cannot escape the output contract,
    which is enforced on the response regardless of prompt content.
    """
    agent = workspace.agent
    # Params render as flat strings ("ref:Person (required)") rather than
    # nested objects: models mirror object keys back as values, which
    # produces invalid param payloads.
    actions = {
        name: {
            "description": spec.description,
            "params": {
                pname: _param_summary(p)
                for pname, p in spec.params.items()
            },
        }
        for name, spec in agent.permitted_actions.items()
    }
    subject_ids = sorted(
        {c["subject"]["id"] for c in candidates}
    )
    contract = {
        "type": "object",
        "required": agent.task.deliverables,
        "shapes": {
            "explanation": "string",
            "hypotheses": [
                {"statement": "string", "evidence": ["fact_key or observation_id"]}
            ],
            "follow_up_questions": ["string"],
            "action_proposals": [
                {"action": "string", "params": "object", "reason": "string"}
            ],
        },
    }
    return "\n".join(
        [
            f"You are the agent {agent.name!r} diagnosing the projection "
            f"{agent.projection!r}.",
            f"Goal: {agent.task.goal}",
            "",
            "Rules:",
            "- Respond with a single JSON object and nothing else.",
            "- Required keys: " + ", ".join(agent.task.deliverables) + ".",
            "- Report hypotheses under 'hypotheses' as unconfirmed hypotheses, "
            "never as established facts; cite evidence ids when possible.",
            "- Only propose actions listed under permitted_actions, with "
            "params matching each action's declared schema; a 'ref' param "
            "takes the referenced entity's id as a plain string value.",
            "- Every action proposal must name the job it concerns via the "
            "ref parameter declared for it (allowed subject ids: "
            + (", ".join(subject_ids) or "<none>")
            + ").",
            "- Proposing an action does not approve or execute it; a human "
            "reviews proposals later.",
            "",
            "Output contract:",
            json.dumps(contract, sort_keys=True),
            "",
            "Permitted actions:",
            json.dumps(actions, sort_keys=True),
            "",
            _proposal_example(agent),
            "",
            "Candidates (JSON):",
            json.dumps(candidates, sort_keys=True, default=str),
        ]
    )


def _param_summary(spec: PropertySpec) -> str:
    text = f"{spec.type}:{spec.ref}" if spec.ref else spec.type
    if spec.description:
        text += f" — {spec.description}"
    return text + (" (required)" if spec.required else " (optional)")


def _proposal_example(agent) -> str:
    """A concrete well-formed proposal so the model returns plain scalar
    param values (a 'ref' param is the entity id string, not an object)."""
    lines = []
    for name, spec in agent.permitted_actions.items():
        params = {}
        for pname, p in spec.params.items():
            if p.type == "ref":
                params[pname] = f"<{p.ref} id>"
            elif p.type == "integer":
                params[pname] = 0
            elif p.type == "number":
                params[pname] = 0.0
            elif p.type == "boolean":
                params[pname] = True
            else:
                params[pname] = "..."
        example = {"action": name, "params": params, "reason": "..."}
        lines.append(
            f"Example {name} proposal (a 'ref' param value is the entity id "
            f"string): {json.dumps(example, sort_keys=True)}"
        )
    return "\n".join(lines)


def _input_digest(
    workspace: Workspace,
    projection_run_id: str | None,
    candidates: list[dict[str, Any]],
) -> str:
    """Digest of everything that defines this diagnosis input."""
    agent = workspace.agent
    raw = json.dumps(
        {
            "prompt_version": PROMPT_VERSION,
            "agent": {
                "name": agent.name,
                "projection": agent.projection,
                "task": dataclasses.asdict(agent.task),
                "permitted_actions": {
                    name: dataclasses.asdict(spec)
                    for name, spec in agent.permitted_actions.items()
                },
            },
            "projection_run_id": projection_run_id,
            "candidates": candidates,
        },
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _extract_json(text: str) -> Any:
    """Parse the model's output as one JSON value.

    Models routinely wrap JSON in a fenced code block or stray prose, so
    extraction tries the raw text, then the text inside a code fence, then
    the first-to-last brace span. Anything else is left to the caller as
    ``None`` (unparseable).
    """
    text = text.strip()
    fenced = re.fullmatch(
        r"```(?:json|JSON)?\s*\n(?P<body>.*?)\n?\s*```", text, re.DOTALL
    )
    if fenced:
        text = fenced.group("body").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _validate_output(
    workspace: Workspace,
    raw: str,
    candidates: list[dict[str, Any]],
    store: CanonicalStore,
    diag: Diagnostics,
    result: DiagnosisResult,
) -> dict[str, Any]:
    """Parse and validate the raw output against the task deliverables and
    permitted action schemas.

    Contract-level failures (unparseable output, wrong top-level type,
    missing or mis-typed required deliverables) produce an error diagnostic
    and reject the whole output. Per-item failures — an unpermitted action,
    a bad param — reject only that item and are recorded in
    ``result.rejected``.
    """
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        diag.error(
            "DIAGNOSIS.INVALID_OUTPUT",
            "model output is not a JSON object",
        )
        return {}

    deliverables: dict[str, Any] = {}
    wanted = workspace.agent.task.deliverables
    for name in wanted:
        if name == "action_proposals":
            continue
        value = parsed.get(name)
        deliverables[name] = _check_deliverable(name, value, diag)

    proposals_node = parsed.get("action_proposals", [])
    if "action_proposals" in wanted:
        if not isinstance(proposals_node, list):
            diag.error(
                "DIAGNOSIS.INVALID_OUTPUT",
                "deliverable 'action_proposals' must be a list",
            )
        else:
            for index, item in enumerate(proposals_node):
                _check_proposal(
                    workspace, item, index, candidates, store, diag, result
                )
    elif proposals_node:
        result.rejected.append(
            {
                "index": None,
                "reason": "task does not declare 'action_proposals' as a "
                "deliverable",
            }
        )
        diag.warning(
            "DIAGNOSIS.UNEXPECTED_PROPOSALS",
            "model returned action proposals the task does not ask for",
        )
    return deliverables


def _check_deliverable(
    name: str, value: Any, diag: Diagnostics
) -> Any:
    """Validate one non-proposal deliverable; returns the value or None."""
    if name == "explanation":
        if not isinstance(value, str) or not value.strip():
            diag.error(
                "DIAGNOSIS.INVALID_OUTPUT",
                "deliverable 'explanation' must be a non-empty string",
            )
            return None
        return value
    if name == "hypotheses":
        if not isinstance(value, list):
            diag.error(
                "DIAGNOSIS.INVALID_OUTPUT",
                "deliverable 'hypotheses' must be a list",
            )
            return None
        accepted: list[dict[str, Any]] = []
        for i, item in enumerate(value):
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("statement"), str)
                or not item["statement"].strip()
            ):
                diag.error(
                    "DIAGNOSIS.INVALID_OUTPUT",
                    f"hypotheses[{i}] must be an object with a non-empty "
                    "'statement' string",
                )
                continue
            evidence = item.get("evidence", [])
            if not isinstance(evidence, list) or not all(
                isinstance(e, str) for e in evidence
            ):
                diag.error(
                    "DIAGNOSIS.INVALID_OUTPUT",
                    f"hypotheses[{i}].evidence must be a list of strings",
                )
                continue
            accepted.append(
                {"statement": item["statement"], "evidence": evidence}
            )
        return accepted
    if name == "follow_up_questions":
        if not isinstance(value, list) or not all(
            isinstance(q, str) for q in value
        ):
            diag.error(
                "DIAGNOSIS.INVALID_OUTPUT",
                "deliverable 'follow_up_questions' must be a list of strings",
            )
            return None
        return value
    return value


def _check_proposal(
    workspace: Workspace,
    item: Any,
    index: int,
    candidates: list[dict[str, Any]],
    store: CanonicalStore,
    diag: Diagnostics,
    result: DiagnosisResult,
) -> None:
    """Validate one action proposal; accepted items land in
    ``result.proposals``, everything else in ``result.rejected``."""

    def reject(reason: str) -> None:
        result.rejected.append({"index": index, "item": item, "reason": reason})
        diag.warning(
            "DIAGNOSIS.PROPOSAL_REJECTED",
            f"action_proposals[{index}] rejected: {reason}",
        )

    if not isinstance(item, dict):
        reject("proposal must be an object")
        return
    action = item.get("action")
    params = item.get("params")
    reason = item.get("reason")
    spec = workspace.agent.permitted_actions.get(action)
    if not isinstance(action, str) or spec is None:
        reject(
            f"action {action!r} is not in permitted_actions "
            f"({', '.join(sorted(workspace.agent.permitted_actions))})"
        )
        return
    if reason is not None and not isinstance(reason, str):
        reject("'reason' must be a string when present")
        return
    if not isinstance(params, dict):
        reject("'params' must be an object")
        return
    unknown = sorted(set(params) - set(spec.params))
    if unknown:
        reject(f"unknown params {unknown} for action {action!r}")
        return
    subject_entity = workspace.domain.projections[
        workspace.agent.projection
    ].subject
    candidate_ids = {c["subject"]["id"] for c in candidates}
    for pname, pspec in spec.params.items():
        value = params.get(pname)
        if value is None:
            if pspec.required:
                reject(f"missing required param {pname!r}")
                return
            continue
        if not _param_type_ok(value, pspec):
            reject(f"param {pname!r} must be {pspec.type}")
            return
        if pspec.type == "ref":
            problem = _check_ref(
                store, pspec, value, subject_entity, candidate_ids
            )
            if problem is not None:
                reject(f"param {pname!r}: {problem}")
                return
    result.proposals.append(
        ActionProposal(action=action, params=params, reason=reason)
    )


def _param_type_ok(value: Any, spec: PropertySpec) -> bool:
    if spec.type == "ref":
        return isinstance(value, str) and bool(value)
    if spec.type == "string":
        return isinstance(value, str)
    if spec.type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if spec.type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if spec.type == "boolean":
        return isinstance(value, bool)
    if spec.type in ("date", "datetime"):
        return isinstance(value, str)
    return False


def _check_ref(
    store: CanonicalStore,
    spec: PropertySpec,
    value: str,
    subject_entity: str,
    candidate_ids: set[str],
) -> str | None:
    """Validate a ref param: the entity must exist, and a ref to the
    projection subject entity must cite one of the diagnosed candidates."""
    try:
        entity = store.entity_state(spec.ref or "", value)
    except Exception as exc:
        return f"cannot resolve ref {spec.ref}/{value}: {exc}"
    if entity is None:
        return f"unknown {spec.ref} entity {value!r}"
    if spec.ref == subject_entity and value not in candidate_ids:
        return (
            f"{spec.ref} {value!r} is not among the diagnosed candidates "
            f"({', '.join(sorted(candidate_ids)) or 'none'})"
        )
    return None


def _emit_trace(
    trace: TraceSink, result: DiagnosisResult, diag: Diagnostics
) -> None:
    try:
        trace.emit(
            "diagnosis",
            "diagnosis.failed" if diag.has_errors else "diagnosis.completed",
            {
                "run_id": result.run_id,
                "projection_run_id": result.projection_run_id,
                "provider": result.provider,
                "model": result.model,
                "status": result.status,
                "candidates": result.candidate_count,
                "proposals": len(result.proposals),
                "rejected": len(result.rejected),
                "recorded": result.recorded,
                "errors": [
                    d.code for d in diag.items if d.severity == "error"
                ],
            },
        )
    except OSError as exc:
        diag.warning(
            "DIAGNOSIS.TRACE_ERROR",
            f"cannot write trace: {exc.strerror or exc}",
        )


__all__ = [
    "ActionProposal",
    "DiagnosisProvider",
    "DiagnosisResult",
    "PROMPT_VERSION",
    "ProviderError",
    "ProviderRequest",
    "build_prompt",
    "get_diagnosis_run",
    "run_diagnosis",
]
