"""Write-back execution for approved action proposals.

This stage turns a persisted, human-approved proposal into a concrete
effect through a narrow adapter boundary:

- ``execute`` loads the persisted proposal, checks the execution's
  idempotency state, rechecks the approval gate (``check_approval``)
  against the exact stored payload immediately before writing, and only
  then invokes the adapter.
- Every adapter-reaching attempt appends *two* rows to ``executions``:
  an ``attempting`` intent row *before* the adapter is invoked and an
  outcome row after. If the intent row cannot be persisted the adapter
  is never called — an unrecorded adapter invocation would be invisible
  to the replay and uncertainty gates on the next run.
- Outcomes are honest about delivery: ``succeeded`` means the adapter
  returned a receipt; ``failed`` means the adapter attested no side
  effect occurred (safe to retry); ``uncertain`` means the adapter
  outcome is unknown (interruption, unexpected exception, or a stranded
  ``attempting`` row whose outcome never got recorded). The system never
  claims exactly-once delivery.
- A successful execution is idempotent: repeating ``execute`` returns
  the persisted receipt without touching the adapter, so repeated
  successful execution cannot duplicate output.
- After ``uncertain`` (or a stranded ``attempting`` row), automatic
  retry is refused for adapters that do not declare themselves
  ``idempotent`` — the previous write may have landed and a retry could
  duplicate it. An idempotent adapter (one that dedupes on the
  idempotency key) may retry safely: a re-delivered key reports the
  existing effect instead of creating a second one. For non-idempotent
  adapters the refusal is permanent for that payload; the recovery is a
  new diagnosis run producing a new proposal.

Refusals — the approval gate failing or a prior
``uncertain``/``attempting`` attempt against a non-idempotent adapter —
never reach the adapter and leave no ``executions`` row; they are still
auditable through ``execution.refused`` trace events, matching the
``decision.failed`` convention.

Execution identity binds the destination: an adapter may expose a
``destination`` (e.g. a CSV file path), folded into the execution id and
idempotency key, so a different destination is a different execution
with its own attempt history rather than a replay of a foreign receipt.

The executor never mutates canonical entities, events, or source data
and never fabricates business outcomes: the only writes are the
``executions`` rows, the trace, and whatever the adapter itself writes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .canonical import (
    CanonicalStore,
    SqliteCanonicalStore,
    store_error_code,
)
from .decisions import backfill_proposals, check_approval
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR
from .model import Workspace
from .trace import JsonlTraceSink, TraceSink

EXECUTION_STATUSES = ("attempting", "succeeded", "failed", "uncertain")

_DIGEST_VERSION = "v1"


class AdapterFailed(Exception):
    """The adapter is certain no side effect occurred.

    Raised for failures detected *before* any write — an unsupported
    action, invalid params, an output that cannot be opened. The
    executor records the attempt as ``failed`` and permits a retry. Any
    other exception from ``deliver`` is recorded as ``uncertain`` instead.

    The message is persisted verbatim and shown to the operator, so it
    must name *what* failed — never embed param values or secrets.
    """


@dataclass(frozen=True)
class AdapterRequest:
    """The transport-independent request handed to a write-back adapter.

    Carries the approved payload plus the execution identity the adapter
    should report in its receipt — the idempotency key lets an adapter
    dedupe its own output as defense in depth.
    """

    execution_id: str
    idempotency_key: str
    attempt: int
    proposal_id: str
    action: str
    params: dict[str, Any]
    payload_digest: str
    subject_entity: str | None
    subject_id: str | None
    related_entities: list[dict[str, str]]
    reason: str | None


class ExecutionAdapter(Protocol):
    """Boundary between the core and one write-back destination.

    An adapter implements ``deliver`` and exposes its stable identity via
    ``name``. It returns a plain-dict receipt on success, raises
    ``AdapterFailed`` when it is certain nothing was written, and lets
    any other exception propagate — the executor records that as
    ``uncertain``, because the adapter itself cannot say whether the
    effect reached the destination.
    """

    @property
    def name(self) -> str: ...

    @property
    def idempotent(self) -> bool:
        """Whether re-delivering the same idempotency key cannot create
        a duplicate effect. When true, retrying after an ``uncertain``
        outcome is safe — the adapter reports the existing effect."""
        ...

    @property
    def destination(self) -> str | None:
        """Optional stable identifier for the destination this adapter
        writes to (e.g. a file path). When present it is folded into the
        execution identity, so a different destination gets its own
        execution rather than replaying another destination's receipt."""
        ...

    def deliver(self, request: AdapterRequest) -> dict[str, Any]:
        """Perform the write-back for one approved attempt.

        Returns a receipt describing the concrete effect. The receipt is
        persisted verbatim, so it must contain only inspectable
        references (ids, paths, timestamps) — never secrets.
        """
        ...


REMINDER_COLUMNS = (
    "reminder_id",
    "proposal_id",
    "execution_id",
    "idempotency_key",
    "action",
    "target",
    "related_entity",
    "reason",
    "created_at",
)


class CsvReminderAdapter:
    """Local mock notification adapter: appends reminders to a CSV file.

    The v0.1 write-back target — a ``SendReminder`` proposal becomes one
    inspectable row with proposal, job (related entity), and target
    references. The adapter dedupes on the idempotency key and returns a
    receipt with the written row's identity; it refuses anything it
    cannot honor honestly with ``AdapterFailed``.

    Values land in a CSV that operators open in a spreadsheet, so cell
    values that would be interpreted as formulas (leading ``=``, ``+``,
    ``-``, ``@``, or whitespace/control characters) are written with a
    leading ``'`` to neutralize them.
    """

    name = "csv-reminders"
    idempotent = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @property
    def destination(self) -> str:
        return str(self.path)

    def deliver(self, request: AdapterRequest) -> dict[str, Any]:
        if request.action != "SendReminder":
            raise AdapterFailed(
                f"unsupported action {request.action!r}: "
                "this adapter only delivers SendReminder"
            )
        params = request.params
        target = params.get("target")
        related = params.get("related_entity")
        reason = params.get("reason")
        # target and related_entity are the references a reminder is
        # meaningless without; reason follows the contract — optional
        # params may be absent, so only its type is validated.
        invalid = [
            name
            for name, value in (
                ("target", target),
                ("related_entity", related),
            )
            if not isinstance(value, str) or not value
        ]
        if reason is not None and not isinstance(reason, str):
            invalid.append("reason")
        if invalid:
            raise AdapterFailed(
                f"SendReminder params missing or invalid: {invalid}"
            )

        reminder_id = "rem-" + request.idempotency_key.removeprefix("idem-")[:12]
        existing = self._find_row(request.idempotency_key)
        if existing is not None:
            # Same idempotency key already delivered: report the existing
            # row instead of appending a duplicate.
            return {
                "reminder_id": existing["reminder_id"],
                "path": str(self.path),
                "deduplicated": True,
                "created_at": existing["created_at"],
            }

        created_at = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            needs_header = (
                not self.path.exists() or self.path.stat().st_size == 0
            )
            handle = self.path.open("a", newline="", encoding="utf-8")
        except OSError as exc:
            # Failed before the file was opened for writing: definitely
            # no row was appended.
            raise AdapterFailed(
                f"cannot open {self.path}: {exc.strerror or exc}"
            ) from exc
        try:
            writer = csv.writer(handle)
            if needs_header:
                writer.writerow(REMINDER_COLUMNS)
            writer.writerow(
                [
                    _csv_safe(v)
                    for v in (
                        reminder_id,
                        request.proposal_id,
                        request.execution_id,
                        request.idempotency_key,
                        request.action,
                        target,
                        related,
                        reason or "",
                        created_at,
                    )
                ]
            )
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            # Any exception between open and fsync propagates as a
            # generic error — the executor records it as ``uncertain``
            # because the row may have reached the file.
            handle.close()
        return {
            "reminder_id": reminder_id,
            "path": str(self.path),
            "deduplicated": False,
            "created_at": created_at,
        }

    def _find_row(self, idempotency_key: str) -> dict[str, str] | None:
        if not self.path.exists():
            return None
        if self.path.stat().st_size:
            with self.path.open("rb") as fb:
                fb.seek(-1, os.SEEK_END)
                if fb.read(1) != b"\n":
                    # A torn final line has no terminator; appending
                    # would merge the new row into it.
                    raise AdapterFailed(
                        f"existing {self.path} ends without a newline: "
                        "a partial row may exist — reconcile manually"
                    )
        try:
            with self.path.open("r", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    # A ragged row (fewer cells than the header or extra
                    # cells) is a torn write — a partial reminder may be
                    # there, so neither dedupe nor a blind append is
                    # honest.
                    if None in row or any(
                        v is None for v in row.values()
                    ):
                        raise AdapterFailed(
                            f"existing {self.path} has a truncated row: "
                            "a partial reminder may exist — reconcile "
                            "manually"
                        )
                    if row["idempotency_key"] != idempotency_key:
                        continue
                    # Structural cells are never empty in a real write;
                    # a match missing one is a torn row, not a receipt.
                    if not all(
                        row.get(c)
                        for c in (
                            "reminder_id",
                            "proposal_id",
                            "execution_id",
                            "idempotency_key",
                            "created_at",
                        )
                    ):
                        raise AdapterFailed(
                            f"existing {self.path} has an incomplete "
                            f"row for idempotency key "
                            f"{idempotency_key!r}: a partial reminder "
                            "may already exist — reconcile manually"
                        )
                    return row
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            # Cannot prove the key is absent; appending could duplicate.
            raise AdapterFailed(
                f"cannot read existing {self.path}: {exc}"
            ) from exc
        return None


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", " ", "\t", "\r", "\n")


def _csv_safe(value: str) -> str:
    """Neutralize a value that a spreadsheet would treat as a formula.

    Model-authored params reach the CSV verbatim; a cell starting with
    ``= + - @`` or a control character executes when opened in Excel or
    Sheets. Prefixing with ``'`` keeps the value inspectable but inert.
    """
    if value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


@dataclass
class ExecutionResult:
    """Outcome of ``execute``: diagnostics plus the execution state.

    ``attempt`` is the attempt number this call ran or replayed (0 when
    refused before any attempt). ``attempts`` is the highest attempt
    number recorded under the execution's idempotency key — on a refusal
    or replay it reports prior recorded attempts, on a new attempt it
    equals ``attempt``.
    """

    diagnostics: list[Diagnostic] = field(default_factory=list)
    proposal_id: str | None = None
    execution_id: str | None = None
    idempotency_key: str | None = None
    adapter: str | None = None
    status: str | None = None
    attempt: int = 0
    attempts: int = 0
    receipt: dict[str, Any] | None = None
    error: str | None = None
    replayed: bool = False
    recorded: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "succeeded" and not any(
            d.severity == "error" for d in self.diagnostics
        )


def execution_identity(
    proposal_id: str,
    payload_digest: str,
    adapter_name: str = "",
    destination: str | None = None,
) -> tuple[str, str]:
    """Deterministic ``(execution_id, idempotency_key)`` for one proposal.

    Derived from the proposal id, the approved payload digest, and the
    adapter identity — not from wall-clock time — so every ``execute``
    call for the same approved payload and destination converges on the
    same execution. A different destination is a different execution:
    replaying one destination's receipt under another would be a lie.
    The adapter name and destination are JSON-encoded separately so no
    pair of distinct values can collide into one key.
    """
    raw = "\n".join(
        [
            _DIGEST_VERSION,
            proposal_id,
            payload_digest,
            json.dumps([adapter_name, destination]),
        ]
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return f"exe-{digest[:16]}", f"idem-{digest[:32]}"


def execute(
    workspace: Workspace,
    proposal_id: str,
    adapter: ExecutionAdapter,
    *,
    store: CanonicalStore | None = None,
    trace: TraceSink | None = None,
) -> ExecutionResult:
    """Execute the persisted proposal through ``adapter``.

    Never raises on bad input or adapter failure: every outcome becomes
    a result carrying diagnostics, and every adapter-reaching attempt is
    persisted. The approval gate is re-evaluated against the stored
    payload immediately before the adapter is invoked.
    """
    diag = Diagnostics()
    result = ExecutionResult(
        proposal_id=proposal_id, adapter=getattr(adapter, "name", None)
    )

    if store is None:
        store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    if trace is None:
        trace = JsonlTraceSink(
            Path(workspace.path) / RUNTIME_DIR / "trace.jsonl"
        )

    def _finish(kind: str) -> ExecutionResult:
        result.diagnostics = diag.items
        _emit_trace(trace, kind, result, diag)
        return result

    try:
        # Repair the proposals index for runs that predate the table —
        # same as decide/proposal_view — before loading the proposal.
        backfill_proposals(workspace, store)
        proposal = store.proposal(proposal_id)
    except Exception as exc:
        diag.error(
            store_error_code(exc, "EXECUTION.STORE_ERROR"),
            f"cannot read canonical store: {exc}",
        )
        result.status = "refused"
        return _finish("execution.refused")
    if proposal is None:
        diag.error(
            "EXECUTION.UNKNOWN_PROPOSAL",
            f"no persisted proposal {proposal_id!r}",
            hint="proposals are recorded when a diagnosis run validates "
            "them; rejected proposals are never persisted",
        )
        result.status = "refused"
        return _finish("execution.refused")

    # The execution identity binds the destination: a different adapter
    # or output file is a different execution with its own attempt
    # history — never a replay of a foreign receipt.
    adapter_name = result.adapter or ""
    result.execution_id, result.idempotency_key = execution_identity(
        proposal_id,
        proposal["payload_digest"],
        adapter_name,
        getattr(adapter, "destination", None),
    )
    try:
        prior = store.executions_for_key(result.idempotency_key)
    except Exception as exc:
        diag.error(
            store_error_code(exc, "EXECUTION.STORE_ERROR"),
            f"cannot read execution history: {exc}",
        )
        result.status = "refused"
        return _finish("execution.refused")
    result.attempts = max((r["attempt"] for r in prior), default=0)
    latest = prior[-1] if prior else None

    if latest is not None and latest["status"] == "succeeded":
        # The approved effect already exists at this destination: replay
        # the persisted receipt instead of writing a duplicate.
        result.status = "succeeded"
        result.receipt = latest["receipt"]
        result.attempt = latest["attempt"]
        result.replayed = True
        return _finish("execution.replayed")

    if latest is not None and latest["status"] in (
        "uncertain",
        "attempting",
    ):
        # The previous attempt may have reached the adapter — or its
        # outcome was never recorded. Blindly retrying could duplicate
        # the write, so this is only safe when the adapter declares
        # itself idempotent (dedupes on the idempotency key).
        if not getattr(adapter, "idempotent", False):
            diag.error(
                "EXECUTION.UNCERTAIN",
                f"execution {result.execution_id} has an uncertain prior "
                "attempt: the adapter may have written already",
                hint="inspect the adapter output (e.g. reminders.csv) "
                "and reconcile manually; this adapter does not declare "
                "idempotent delivery, so automatic retry is refused — "
                "the recovery is a new diagnosis run and proposal",
            )
            result.status = "refused"
            result.attempt = latest["attempt"]
            return _finish("execution.refused")

    # Gate: the persisted approval and payload digest are rechecked here,
    # immediately before the adapter can write. A proposal approved then
    # rejected, or a payload that no longer matches, fails closed.
    check = check_approval(
        workspace,
        proposal_id,
        action=proposal["action"],
        params=proposal["params"],
        store=store,
    )
    if not check.ok:
        diag.items.extend(check.diagnostics)
        result.status = "refused"
        return _finish("execution.refused")

    attempt = result.attempts + 1
    result.attempt = attempt
    request = AdapterRequest(
        execution_id=result.execution_id,
        idempotency_key=result.idempotency_key,
        attempt=attempt,
        proposal_id=proposal_id,
        action=proposal["action"],
        params=proposal["params"],
        payload_digest=proposal["payload_digest"],
        subject_entity=proposal.get("subject_entity"),
        subject_id=proposal.get("subject_id"),
        related_entities=proposal.get("related_entities") or [],
        reason=proposal.get("reason"),
    )

    # Intent first: persist that the adapter is about to be invoked. If
    # this row cannot be written the adapter is never called — otherwise
    # an unrecorded adapter write would bypass every gate on the next
    # run. A crash or a failed outcome write leaves this row as the
    # latest state, which the gate above treats like ``uncertain``.
    if not _record_row(store, request, result, "attempting", None, None, diag):
        result.status = "refused"
        return _finish("execution.refused")
    result.attempts = attempt

    receipt: dict[str, Any] | None = None
    error: str | None = None
    try:
        receipt = adapter.deliver(request)
    except AdapterFailed as exc:
        # The adapter attests nothing was written — safe to record as
        # failed and to retry later. The message is adapter-controlled
        # and operator-facing, but capped so a careless adapter cannot
        # dump an unbounded payload into the record.
        result.status = "failed"
        error = str(exc)[:512]
    except Exception as exc:
        # The adapter cannot say whether the effect landed. Record the
        # exception class only: an arbitrary adapter's message could
        # embed request details (the diagnosis provider precedent).
        result.status = "uncertain"
        error = f"unexpected {type(exc).__name__}"
    except BaseException as exc:
        # Interruption (KeyboardInterrupt/SystemExit): the outcome is
        # unknown, so record it honestly as uncertain before letting the
        # interruption propagate.
        result.status = "uncertain"
        error = f"interrupted: {type(exc).__name__}"
        _record_row(
            store, request, result, result.status, receipt, error, diag
        )
        result.diagnostics = diag.items
        _emit_trace(trace, "execution.uncertain", result, diag)
        raise
    else:
        result.status = "succeeded"

    if not _record_row(
        store, request, result, result.status, receipt, error, diag
    ):
        # The adapter outcome stands but its evidence could not be
        # persisted: report uncertain, not succeeded — the stranded
        # intent row already blocks the next run, and claiming success
        # would hide an effect with no receipt on record.
        result.status = "uncertain"
    result.receipt = receipt
    result.error = error
    kind = {
        "succeeded": "execution.succeeded",
        "failed": "execution.failed",
        "uncertain": "execution.uncertain",
    }[result.status]
    return _finish(kind)


def _record_row(
    store: CanonicalStore,
    request: AdapterRequest,
    result: ExecutionResult,
    status: str,
    receipt: dict[str, Any] | None,
    error: str | None,
    diag: Diagnostics,
) -> bool:
    """Append one executions row; returns False with a diagnostic on
    store failure."""
    try:
        store.record_execution(
            {
                "execution_id": request.execution_id,
                "idempotency_key": request.idempotency_key,
                "proposal_id": request.proposal_id,
                "action": request.action,
                "adapter": result.adapter or "",
                "attempt": request.attempt,
                "status": status,
                "receipt": receipt,
                "error": error,
                "created_at": datetime.now(timezone.utc),
            }
        )
        result.recorded = True
        return True
    except Exception as exc:
        # Evidence of this attempt could not be persisted — that is
        # itself an error the operator must see.
        diag.error(
            store_error_code(exc, "EXECUTION.STORE_ERROR"),
            f"execution attempt {status!r} could not be recorded: {exc}",
        )
        return False


def _emit_trace(
    trace: TraceSink, kind: str, result: ExecutionResult, diag: Diagnostics
) -> None:
    try:
        trace.emit(
            "execution",
            kind,
            {
                "execution_id": result.execution_id,
                "idempotency_key": result.idempotency_key,
                "proposal_id": result.proposal_id,
                "adapter": result.adapter,
                "attempt": result.attempt,
                "status": result.status,
                "replayed": result.replayed,
                "recorded": result.recorded,
                "errors": [
                    d.code for d in diag.items if d.severity == "error"
                ],
            },
        )
    except OSError as exc:
        diag.warning(
            "EXECUTION.TRACE_ERROR",
            f"cannot write trace: {exc.strerror or exc}",
        )


__all__ = [
    "AdapterFailed",
    "AdapterRequest",
    "CsvReminderAdapter",
    "EXECUTION_STATUSES",
    "ExecutionAdapter",
    "ExecutionResult",
    "REMINDER_COLUMNS",
    "execute",
    "execution_identity",
]
