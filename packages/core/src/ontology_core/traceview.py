"""Read-side trace assembly: inspect and export a linked run history.

Every stage of the loop already leaves durable artifacts — the append-only
journal at ``.ontology/trace.jsonl``, immutable observation evidence and
raw snapshots, and the canonical store's facts, frozen runs, proposals,
decisions, execution attempts, and evaluation reports. This module
reassembles them — strictly read-only — into one linked view, so a run can
be followed from source observation through outcome report.

The workspace definitions are never loaded: history stays inspectable and
unchanged after the domain, mapping, evaluation, or agent files are edited
or broken — only the recorded artifacts and their digests define what was
observed. Nothing here writes to the runtime directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import CanonicalStore, SqliteCanonicalStore
from .diagnostics import Diagnostic, Diagnostics
from .evidence import RUNTIME_DIR, FileEvidenceStore, _atomic_write

TRACE_FORMAT = "ontology-trace"
TRACE_FORMAT_VERSION = 1

# Nature labels keep the four voices of the loop distinguishable: what was
# observed, what was derived deterministically, what the model proposed,
# what a human decided, what was written back, and what was evaluated.
NATURE_OBSERVED = "observed"
NATURE_DERIVED = "derived"
NATURE_AI = "ai"
NATURE_HUMAN = "human"
NATURE_WRITEBACK = "writeback"
NATURE_EVALUATED = "evaluated"

_KIND_NATURE = {
    "observation": NATURE_OBSERVED,
    "ingest_run": NATURE_OBSERVED,
    "snapshot": NATURE_OBSERVED,
    "entity": NATURE_OBSERVED,
    "fact": NATURE_OBSERVED,
    "projection_run": NATURE_DERIVED,
    "diagnosis_run": NATURE_AI,
    "proposal": NATURE_AI,
    "decision": NATURE_HUMAN,
    "execution": NATURE_WRITEBACK,
    "evaluation_run": NATURE_EVALUATED,
    "journal_only": "unknown",
}


@dataclass
class TraceAssembly:
    """Everything the trace knows about one workspace runtime.

    Built once by ``assemble_trace``; the view functions below render it.
    ``links`` is the resolved reference graph between artifact ids —
    ``resolved`` is False when an endpoint exists in no stored artifact
    and no journal event, i.e. the reference is dangling.
    """

    workspace: str = ""
    journal: list[dict[str, Any]] = field(default_factory=list)
    journal_errors: list[str] = field(default_factory=list)
    observations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    observation_errors: list[str] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    projection_runs: list[dict[str, Any]] = field(default_factory=list)
    diagnosis_runs: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    unindexed_proposals: list[dict[str, Any]] = field(default_factory=list)
    evaluation_runs: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


def assemble_trace(
    root: str | Path,
    *,
    store: CanonicalStore | None = None,
    evidence: FileEvidenceStore | None = None,
) -> TraceAssembly:
    """Assemble the linked trace of one workspace's runtime directory.

    Read-only: a missing ``.ontology/`` yields an empty assembly, and a
    workspace that has never ingested gets no ``canon.db`` created.
    Corrupt evidence degrades to diagnostics, never tracebacks.
    """
    diag = Diagnostics()
    root = Path(root)
    asm = TraceAssembly(workspace=str(root))
    runtime = root / RUNTIME_DIR

    asm.journal, asm.journal_errors = _read_journal(runtime, diag)
    if evidence is None:
        evidence = FileEvidenceStore(runtime)
    asm.observations, asm.observation_errors = _read_observations(
        evidence, diag
    )
    asm.snapshots = _read_snapshots(evidence, diag)

    if store is None:
        store = SqliteCanonicalStore(runtime, readonly=True)
    _read_store(asm, store, diag)
    _compute_links(asm)
    for link in asm.unresolved:
        diag.warning(
            "TRACE.UNRESOLVED_LINK",
            f"{link['from']} references {link['to']} via {link['via']}, "
            "but the target is in no stored artifact or journal event",
        )
    asm.diagnostics = diag.items
    return asm


# ---------------------------------------------------------------- assembly


def _read_journal(
    runtime: Path, diag: Diagnostics
) -> tuple[list[dict[str, Any]], list[str]]:
    path = runtime / "trace.jsonl"
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return events, errors
    except (OSError, UnicodeDecodeError) as exc:
        detail = getattr(exc, "strerror", None) or exc
        diag.error(
            "TRACE.JOURNAL_UNREADABLE",
            f"cannot read trace journal: {detail}",
        )
        return events, errors
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"trace.jsonl:{lineno}: {exc}")
            diag.warning(
                "TRACE.JOURNAL_CORRUPT",
                f"trace.jsonl line {lineno} is not valid JSON; skipped",
            )
            continue
        if not isinstance(event, dict):
            errors.append(f"trace.jsonl:{lineno}: event is not an object")
            diag.warning(
                "TRACE.JOURNAL_CORRUPT",
                f"trace.jsonl line {lineno} is not a JSON object; skipped",
            )
            continue
        events.append(event)
    return events, errors


def _read_observations(
    evidence: FileEvidenceStore, diag: Diagnostics
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Every persisted observation record, keyed by its ingest run id."""
    runs: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    try:
        files = evidence.observation_files()
    except OSError as exc:
        diag.error(
            "TRACE.EVIDENCE_UNREADABLE",
            f"cannot list observation evidence: {exc.strerror or exc}",
        )
        return runs, errors
    for path in files:
        ingest_id = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            detail = getattr(exc, "strerror", None) or exc
            errors.append(f"{path.name}: {detail}")
            diag.warning(
                "TRACE.EVIDENCE_UNREADABLE",
                f"cannot read observations {path.name}: {detail}",
            )
            continue
        records: list[dict[str, Any]] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name}:{lineno}: {exc}")
                diag.warning(
                    "TRACE.EVIDENCE_CORRUPT",
                    f"{path.name} line {lineno} is not valid JSON; skipped",
                )
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                errors.append(f"{path.name}:{lineno}: not an object")
        runs[ingest_id] = records
    return runs, errors


def _read_snapshots(
    evidence: FileEvidenceStore, diag: Diagnostics
) -> list[dict[str, Any]]:
    """Stored snapshot descriptors; file names carry digest and format."""
    snapshots: list[dict[str, Any]] = []
    try:
        files = evidence.snapshot_files()
    except OSError as exc:
        diag.error(
            "TRACE.EVIDENCE_UNREADABLE",
            f"cannot list snapshot evidence: {exc.strerror or exc}",
        )
        return snapshots
    for path in files:
        entry: dict[str, Any] = {"name": path.name, "path": str(path)}
        stem, _, fmt = path.name.partition(".")
        if stem.startswith("sha256-") and fmt:
            entry["digest"] = "sha256:" + stem[len("sha256-") :]
            entry["format"] = fmt
        try:
            entry["size_bytes"] = path.stat().st_size
        except OSError:
            pass
        snapshots.append(entry)
    return snapshots


def _read_store(
    asm: TraceAssembly, store: CanonicalStore, diag: Diagnostics
) -> None:
    """Fill the canonical sections; each read failure is one diagnostic."""

    def read(what: str, fn):
        try:
            return fn()
        except Exception as exc:
            diag.error(
                "TRACE.STORE_ERROR",
                f"cannot read {what} from canonical store: {exc}",
            )
            return []

    asm.entities = read("entities", store.entities)
    events = read("events", store.events)
    asm.projection_runs = read("projection runs", store.projection_runs)
    asm.diagnosis_runs = read("diagnosis runs", store.diagnosis_runs)
    asm.evaluation_runs = read("evaluation runs", store.evaluation_runs)
    proposals = read("proposals", store.proposals)

    facts: list[dict[str, Any]] = []
    for event in events:
        fact = event.to_dict()
        pairs = read(
            f"observation links for {event.fact_key}",
            lambda f=event.fact_key: store.event_links(f),
        )
        fact["observation_ids"] = [obs for obs, _ in pairs]
        fact["ingest_ids"] = sorted({ing for _, ing in pairs})
        fact["links"] = read(
            f"fact links for {event.fact_key}",
            lambda f=event.fact_key: store.fact_links(f),
        )
        facts.append(fact)
    asm.facts = facts

    for row in proposals:
        proposal = dict(row)
        pid = row["proposal_id"]
        proposal["decisions"] = read(
            f"decisions for {pid}",
            lambda p=pid: store.decisions_for(p),
        )
        proposal["executions"] = read(
            f"executions for {pid}",
            lambda p=pid: store.executions_for_proposal(p),
        )
        proposal["correlated_facts"] = [
            link["fact_key"]
            for link in read(
                f"fact links targeting {pid}",
                lambda p=pid: store.links_for_target(p),
            )
            if link.get("relation") == "correlated_intervention"
        ]
        proposal["status"] = (
            proposal["decisions"][-1]["decision"]
            if proposal["decisions"]
            else "proposed"
        )
        proposal["indexed"] = True
        asm.proposals.append(proposal)

    indexed = {p["proposal_id"] for p in asm.proposals}
    indexed_by_run: dict[str, list[str]] = {}
    for proposal in asm.proposals:
        indexed_by_run.setdefault(proposal["diagnosis_run_id"], []).append(
            _proposal_fingerprint(
                proposal.get("action"),
                proposal.get("params"),
                proposal.get("reason"),
            )
        )
    for run in asm.diagnosis_runs:
        stored = indexed_by_run.get(run["run_id"], [])
        for position, item in enumerate(run.get("proposals") or []):
            index = item.get("index")
            if not isinstance(index, int):
                # Legacy records predate the stored raw index; rejected
                # model items shift the validated-list position, so the
                # position can alias a different proposal's ``-pN`` id.
                # Match on content — each stored row claims at most one
                # raw item — and borrow the positional ``-pN`` name only
                # when no stored row already owns it.
                fingerprint = _proposal_fingerprint(
                    item.get("action"),
                    item.get("params"),
                    item.get("reason"),
                )
                if fingerprint in stored:
                    stored.remove(fingerprint)
                    continue
                index = position
                proposal_id = f"{run['run_id']}-p{index}"
                if proposal_id in indexed:
                    proposal_id = f"{run['run_id']}-pos{position}"
            else:
                proposal_id = f"{run['run_id']}-p{index}"
            if proposal_id in indexed:
                continue
            asm.unindexed_proposals.append(
                {
                    "proposal_id": proposal_id,
                    "diagnosis_run_id": run["run_id"],
                    "proposal_index": index,
                    "action": item.get("action"),
                    "params": item.get("params") or {},
                    "reason": item.get("reason"),
                    "subject_entity": None,
                    "subject_id": None,
                    "evidence": {},
                    "decisions": [],
                    "executions": [],
                    "correlated_facts": [],
                    "status": "unindexed",
                    "indexed": False,
                }
            )


def _proposal_fingerprint(action: Any, params: Any, reason: Any) -> str:
    """Content identity of a proposal for matching raw items to stored rows."""
    return json.dumps(
        {"action": action, "params": params, "reason": reason},
        sort_keys=True,
    )


# ------------------------------------------------------------------- links


def _compute_links(asm: TraceAssembly) -> None:
    """Build the reference graph between artifact ids.

    A link resolves when both endpoints exist — in a stored artifact or,
    for runs that failed before freezing, in the journal. Anything else
    lands in ``unresolved``.
    """
    known: set[str] = set()
    ingest_ids: set[str] = set(asm.observations)
    for records in asm.observations.values():
        for record in records:
            if record.get("observation_id"):
                known.add(record["observation_id"])
            if record.get("ingest_id"):
                ingest_ids.add(record["ingest_id"])
    for entity in asm.entities:
        known.add(f"{entity['entity_type']}/{entity['entity_id']}")
    for fact in asm.facts:
        known.add(fact["fact_key"])
        known.add(f"{fact['subject']['entity']}/{fact['subject']['id']}")
        if fact.get("ingest_id"):
            ingest_ids.add(fact["ingest_id"])
    for run in asm.projection_runs:
        known.add(run["run_id"])
    for run in asm.diagnosis_runs:
        known.add(run["run_id"])
    for proposal in asm.proposals:
        known.add(proposal["proposal_id"])
        for decision in proposal["decisions"]:
            known.add(decision["decision_id"])
        for attempt in proposal["executions"]:
            known.add(attempt["execution_id"])
            known.add(attempt["idempotency_key"])
    for proposal in asm.unindexed_proposals:
        known.add(proposal["proposal_id"])
    for run in asm.evaluation_runs:
        known.add(run["run_id"])
    for snapshot in asm.snapshots:
        if snapshot.get("digest"):
            known.add(snapshot["digest"])
    # Journal events carry ids for runs that failed before freezing; a
    # failed run is inspectable evidence, so journal ids count as known.
    journal_mentions = _journal_ids(asm.journal)
    known |= ingest_ids | set(journal_mentions)

    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def link(source: str | None, target: str | None, via: str) -> None:
        if not source or not target or (source, target, via) in seen:
            return
        seen.add((source, target, via))
        links.append(
            {
                "from": source,
                "to": target,
                "via": via,
                "resolved": source in known and target in known,
            }
        )

    for ingest_id, records in asm.observations.items():
        digests = {r.get("content_digest") for r in records if r.get("content_digest")}
        for digest in sorted(digests):
            link(ingest_id, digest, "snapshot")
        for record in records:
            obs = record.get("observation_id")
            link(obs, ingest_id, "ingest_run")
            for entity in record.get("entities") or []:
                link(obs, f"{entity['entity']}/{entity['id']}", "resolved_entity")

    # An ingest of an empty source writes no observation records; its
    # snapshot digest is then recorded only in the journal.
    for event in asm.journal:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        link(data.get("ingest_id"), data.get("content_digest"), "snapshot")

    for fact in asm.facts:
        fact_key = fact["fact_key"]
        for obs_id in fact["observation_ids"]:
            link(obs_id, fact_key, "asserted")
        for flink in fact["links"]:
            link(fact_key, flink.get("target"), flink.get("relation", "link"))

    for run in asm.projection_runs:
        for entry in run.get("inputs") or []:
            for key in _input_fact_keys(entry):
                link(run["run_id"], key, "input_fact")
        for candidate in run.get("candidates") or []:
            subject = candidate.get("subject") or {}
            link(
                run["run_id"],
                f"{subject.get('entity')}/{subject.get('id')}",
                "candidate",
            )
            evidence = candidate.get("evidence") or {}
            link(run["run_id"], evidence.get("fact_key"), "evidence")
            link(
                run["run_id"],
                evidence.get("entity_observation_id"),
                "evidence",
            )
            for obs_id in evidence.get("observation_ids") or []:
                link(run["run_id"], obs_id, "evidence")
            for ing_id in evidence.get("ingest_ids") or []:
                link(run["run_id"], ing_id, "evidence")

    for run in asm.diagnosis_runs:
        link(run["run_id"], run.get("projection_run_id"), "input_run")
    for proposal in asm.proposals + asm.unindexed_proposals:
        link(proposal["proposal_id"], proposal["diagnosis_run_id"], "from_run")
        evidence = proposal.get("evidence") or {}
        link(proposal["proposal_id"], evidence.get("fact_key"), "evidence")
        link(
            proposal["proposal_id"],
            evidence.get("entity_observation_id"),
            "evidence",
        )
        for obs_id in evidence.get("observation_ids") or []:
            link(proposal["proposal_id"], obs_id, "evidence")
        for ing_id in evidence.get("ingest_ids") or []:
            link(proposal["proposal_id"], ing_id, "evidence")
        if proposal.get("subject_id"):
            link(
                proposal["proposal_id"],
                f"{proposal['subject_entity']}/{proposal['subject_id']}",
                "subject",
            )
        for related in proposal.get("related_entities") or []:
            link(
                proposal["proposal_id"],
                f"{related['entity']}/{related['id']}",
                "related",
            )
        for decision in proposal["decisions"]:
            link(decision["decision_id"], proposal["proposal_id"], "decision_on")
        for attempt in proposal["executions"]:
            link(attempt["execution_id"], proposal["proposal_id"], "execution_of")
            # The idempotency key is the durable identity across attempts.
            link(
                attempt["idempotency_key"],
                attempt["execution_id"],
                "idempotent_identity",
            )
        for fact_key in proposal["correlated_facts"]:
            link(fact_key, proposal["proposal_id"], "correlated_intervention")

    for run in asm.evaluation_runs:
        report = run.get("report") or {}
        subject_entity = (report.get("interval") or {}).get("subject")
        for entry in report.get("inputs") or []:
            if subject_entity and entry.get("subject_id"):
                link(
                    run["run_id"],
                    f"{subject_entity}/{entry.get('subject_id')}",
                    "subject",
                )
            for key in _input_fact_keys(entry):
                link(run["run_id"], key, "input_fact")
        for fact_key in _report_pair_keys(report):
            link(run["run_id"], fact_key, "pair_fact")

    asm.links = links
    asm.unresolved = [link for link in links if not link["resolved"]]


def _input_fact_keys(entry: dict[str, Any]) -> list[str]:
    """Fact keys named by a projection/evaluation per-subject input row."""
    keys: list[str] = []
    for field_name in (
        "start_fact_keys",
        "end_fact_keys",
        "future_start_fact_keys",
        "future_end_fact_keys",
    ):
        keys.extend(entry.get(field_name) or [])
    return keys


def _report_pair_keys(report: dict[str, Any]) -> list[str]:
    """Fact keys bound into cohort pairs of an evaluation report."""
    keys: list[str] = []
    cohorts = report.get("cohorts") or {}
    for cohort in cohorts.values():
        for pair in cohort.get("paired") or []:
            for field_name in ("start_fact_key", "end_fact_key"):
                if pair.get(field_name):
                    keys.append(pair[field_name])
            keys.extend(pair.get("extra_end_fact_keys") or [])
        for item in cohort.get("pending") or []:
            if item.get("start_fact_key"):
                keys.append(item["start_fact_key"])
        for item in cohort.get("invalid") or []:
            if item.get("start_fact_key"):
                keys.append(item["start_fact_key"])
            keys.extend(item.get("end_fact_keys") or [])
        for item in cohort.get("ambiguous") or []:
            keys.extend(item.get("start_fact_keys") or [])
            keys.extend(item.get("end_fact_keys") or [])
    for item in report.get("unpaired_ends") or []:
        keys.extend(item.get("end_fact_keys") or [])
    return keys


def _journal_ids(journal: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Map every id string appearing in journal data to event indexes."""
    found: dict[str, list[int]] = {}
    for index, event in enumerate(journal):
        for value in _deep_strings(event.get("data")):
            if _looks_like_id(value):
                found.setdefault(value, []).append(index)
    return found


def _looks_like_id(value: str) -> bool:
    # Proposal ids are ``<diagnosis run>-pN`` — covered by the dgx- prefix.
    return value.startswith(
        (
            "obs-",
            "ing-",
            "fact-",
            "prj-",
            "dgx-",
            "dec-",
            "exe-",
            "idem-",
            "evl-",
            "sha256:",
        )
    )


def _deep_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_deep_strings(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_deep_strings(item))
        return out
    return []


def _mentions(event: dict[str, Any], ids: set[str]) -> bool:
    return bool(ids & set(_deep_strings(event.get("data"))))


def _journal_for(journal: list[dict[str, Any]], ids: set[str]) -> list[dict[str, Any]]:
    """Journal events whose data mentions any of ``ids``."""
    return [event for event in journal if _mentions(event, ids)]


# ------------------------------------------------------------------- views


def trace_overview(asm: TraceAssembly) -> dict[str, Any]:
    """Stage-by-stage counts and failure totals for one workspace."""
    failed_journal = [
        e for e in asm.journal if str(e.get("kind", "")).endswith(".failed")
    ]
    proposals = asm.proposals + asm.unindexed_proposals
    attempts = [a for p in asm.proposals for a in p["executions"]]
    status_counts: dict[str, int] = {}
    for proposal in proposals:
        status_counts[proposal["status"]] = (
            status_counts.get(proposal["status"], 0) + 1
        )
    return {
        "workspace": asm.workspace,
        "ok": asm.ok,
        "journal": {
            "events": len(asm.journal),
            "unreadable_lines": len(asm.journal_errors),
            "failed_events": len(failed_journal),
        },
        "evidence": {
            "ingest_runs": sorted(asm.observations),
            "observations": sum(len(r) for r in asm.observations.values()),
            "snapshots": len(asm.snapshots),
        },
        "canonical": {
            "entities": len(asm.entities),
            "facts": len(asm.facts),
            "fact_links": sum(len(f["links"]) for f in asm.facts),
        },
        "projection_runs": [
            {
                "run_id": r["run_id"],
                "projection": r["projection"],
                "as_of": r["as_of"],
                "candidates": len(r["candidates"]),
            }
            for r in asm.projection_runs
        ],
        "diagnosis_runs": [
            {
                "run_id": r["run_id"],
                "projection_run_id": r["projection_run_id"],
                "provider": r["provider"],
                "model": r["model"],
                "status": r["status"],
            }
            for r in asm.diagnosis_runs
        ],
        "proposals": {
            "total": len(proposals),
            "by_status": status_counts,
            "unindexed": len(asm.unindexed_proposals),
        },
        "decisions": sum(len(p["decisions"]) for p in asm.proposals),
        "executions": {
            "attempts": len(attempts),
            "by_status": _count_by(a["status"] for a in attempts),
        },
        "evaluation_runs": [
            {
                "run_id": r["run_id"],
                "evaluation": r["evaluation"],
                "as_of": r["as_of"],
                "overall": (r.get("report") or {}).get("overall", {}).get("status"),
            }
            for r in asm.evaluation_runs
        ],
        "links": {"total": len(asm.links), "unresolved": len(asm.unresolved)},
        "diagnostics": [d.to_dict() for d in asm.diagnostics],
    }


def trace_subject(
    asm: TraceAssembly, entity: str, subject_id: str
) -> dict[str, Any]:
    """Follow one subject from observation through evaluation.

    Sections are ordered the way the loop runs: what was observed, what
    was derived, what the model proposed, what a human decided, what was
    written back, and what was evaluated.
    """
    subject_key = f"{entity}/{subject_id}"
    facts = [
        f
        for f in asm.facts
        if f["subject"]["entity"] == entity and f["subject"]["id"] == subject_id
    ]
    observations = [
        record
        for records in asm.observations.values()
        for record in records
        if _record_touches(record, entity, subject_id)
    ]
    ingest_ids = sorted({r["ingest_id"] for r in observations if r.get("ingest_id")})
    snapshot_digests = sorted(
        {r["content_digest"] for r in observations if r.get("content_digest")}
    )

    projections = []
    projection_ids: set[str] = set()
    for run in asm.projection_runs:
        role = None
        evidence = None
        for candidate in run.get("candidates") or []:
            subject = candidate.get("subject") or {}
            if subject.get("entity") == entity and subject.get("id") == subject_id:
                role = "candidate"
                evidence = candidate.get("evidence")
                break
        if role is None and run.get("subject") == entity:
            for entry in run.get("inputs") or []:
                if entry.get("subject_id") == subject_id:
                    role = "input"
                    break
        if role is None:
            continue
        projection_ids.add(run["run_id"])
        projections.append(
            {
                "run_id": run["run_id"],
                "projection": run["projection"],
                "as_of": run["as_of"],
                "created_at": run["created_at"],
                "definition_digest": run["definition_digest"],
                "role": role,
                "evidence": evidence,
            }
        )

    all_proposals = asm.proposals + asm.unindexed_proposals
    subject_proposals = [
        p for p in all_proposals if _proposal_touches(p, entity, subject_id)
    ]
    proposal_ids = {p["proposal_id"] for p in subject_proposals}
    run_ids_of_proposals = {p["diagnosis_run_id"] for p in subject_proposals}

    diagnoses = []
    for run in asm.diagnosis_runs:
        cited = run["run_id"] in run_ids_of_proposals
        covers = run.get("projection_run_id") in projection_ids
        if not cited and not covers:
            continue
        diagnoses.append(
            {
                "run_id": run["run_id"],
                "projection_run_id": run["projection_run_id"],
                "provider": run["provider"],
                "model": run["model"],
                "status": run["status"],
                "provider_called": run["provider_called"],
                "prompt_version": run["prompt_version"],
                "input_digest": run["input_digest"],
                "deliverables": run["deliverables"],
                "rejected": run["rejected"],
                "proposals": [
                    p["proposal_id"]
                    for p in all_proposals
                    if p["diagnosis_run_id"] == run["run_id"]
                ],
                "created_at": run["created_at"],
            }
        )

    decisions = [d for p in subject_proposals for d in p["decisions"]]
    executions = [a for p in subject_proposals for a in p["executions"]]
    correlated = sorted(
        {key for p in subject_proposals for key in p["correlated_facts"]}
    )

    evaluations = []
    for run in asm.evaluation_runs:
        report = run.get("report") or {}
        interval = report.get("interval") or {}
        entry = next(
            (
                e
                for e in report.get("inputs") or []
                if e.get("subject_id") == subject_id
                and interval.get("subject") == entity
            ),
            None,
        )
        if entry is None:
            continue
        evaluations.append(
            {
                "run_id": run["run_id"],
                "evaluation": run["evaluation"],
                "as_of": run["as_of"],
                "boundary": run["boundary"],
                "contract_digest": run["contract_digest"],
                "overall": (report.get("overall") or {}).get("status"),
                "subject": _subject_outcome(report, subject_id),
            }
        )

    collected = (
        {subject_key}
        | {f["fact_key"] for f in facts}
        | {r["observation_id"] for r in observations if r.get("observation_id")}
        | set(ingest_ids)
        | set(snapshot_digests)
        | projection_ids
        | {d["run_id"] for d in diagnoses}
        | proposal_ids
        | {d["decision_id"] for d in decisions}
        | {a["execution_id"] for a in executions}
        | {a["idempotency_key"] for a in executions}
        | {e["run_id"] for e in evaluations}
        | set(correlated)
    )
    journal = [
        event
        for event in asm.journal
        if _mentions(event, collected)
        or _event_names_subject(event, entity, subject_id)
    ]
    chain_links = [
        link
        for link in asm.links
        if link["from"] in collected or link["to"] in collected
    ]
    return {
        "subject": {"entity": entity, "id": subject_id},
        "entity": _entity_state(asm, entity, subject_id),
        "observations": observations,
        "facts": facts,
        "ingests": ingest_ids,
        "snapshots": snapshot_digests,
        "projections": projections,
        "diagnoses": diagnoses,
        "proposals": subject_proposals,
        "decisions": decisions,
        "executions": executions,
        "correlated_facts": correlated,
        "evaluations": evaluations,
        "journal": journal,
        "links": chain_links,
        "unresolved": [l for l in chain_links if not l["resolved"]],
    }


def trace_anchor(asm: TraceAssembly, anchor: str) -> dict[str, Any] | None:
    """Inspect one artifact id and its direct links, or None if unknown."""
    kind = _classify(asm, anchor)
    if kind is None:
        return None
    record = _anchor_record(asm, anchor, kind)
    links = [
        link
        for link in asm.links
        if link["from"] == anchor or link["to"] == anchor
    ]
    journal = _journal_for(asm.journal, {anchor})
    view = {
        "id": anchor,
        "kind": kind,
        "nature": _KIND_NATURE.get(kind, "unknown"),
        "record": record,
        "links": links,
        "unresolved": [l for l in links if not l["resolved"]],
        "journal": journal,
    }
    subjects = _anchor_subjects(asm, anchor, kind, record)
    if subjects:
        view["subjects"] = subjects
    return view


def _classify(asm: TraceAssembly, anchor: str) -> str | None:
    if any(r["observation_id"] == anchor for rs in asm.observations.values() for r in rs):
        return "observation"
    if anchor in asm.observations or any(
        anchor in f["ingest_ids"] for f in asm.facts
    ) or any(anchor == e.get("last_ingest_id") for e in asm.entities):
        return "ingest_run"
    for snapshot in asm.snapshots:
        if anchor == snapshot.get("digest") or anchor == snapshot.get("name"):
            return "snapshot"
    for fact in asm.facts:
        if fact["fact_key"] == anchor:
            return "fact"
    for entity in asm.entities:
        if anchor == f"{entity['entity_type']}/{entity['entity_id']}":
            return "entity"
    for run in asm.projection_runs:
        if run["run_id"] == anchor:
            return "projection_run"
    for run in asm.diagnosis_runs:
        if run["run_id"] == anchor:
            return "diagnosis_run"
    for proposal in asm.proposals + asm.unindexed_proposals:
        if proposal["proposal_id"] == anchor:
            return "proposal"
    for proposal in asm.proposals:
        for decision in proposal["decisions"]:
            if decision["decision_id"] == anchor:
                return "decision"
        for attempt in proposal["executions"]:
            if anchor in (attempt["execution_id"], attempt["idempotency_key"]):
                return "execution"
    for run in asm.evaluation_runs:
        if run["run_id"] == anchor:
            return "evaluation_run"
    if "/" in anchor:
        # A subject with facts but no entity row is still inspectable.
        if any(
            f"{f['subject']['entity']}/{f['subject']['id']}" == anchor
            for f in asm.facts
        ):
            return "entity"
    if anchor in _journal_ids(asm.journal):
        return "journal_only"
    return None


def _anchor_record(
    asm: TraceAssembly, anchor: str, kind: str
) -> dict[str, Any] | None:
    if kind == "observation":
        return next(
            r
            for rs in asm.observations.values()
            for r in rs
            if r["observation_id"] == anchor
        )
    if kind == "ingest_run":
        records = asm.observations.get(anchor, [])
        return {
            "ingest_id": anchor,
            "observations": len(records),
            "accepted": sum(1 for r in records if r.get("status") == "accepted"),
            "rejected": sum(1 for r in records if r.get("status") == "rejected"),
            "content_digests": sorted(
                {r["content_digest"] for r in records if r.get("content_digest")}
            ),
            "recorded_in_journal": any(
                anchor in _deep_strings(e.get("data")) for e in asm.journal
            ),
        }
    if kind == "snapshot":
        return next(
            s
            for s in asm.snapshots
            if anchor in (s.get("digest"), s.get("name"))
        )
    if kind == "fact":
        return next(f for f in asm.facts if f["fact_key"] == anchor)
    if kind == "entity":
        entity, _, subject_id = anchor.partition("/")
        return _entity_state(asm, entity, subject_id) or {
            "entity_type": entity,
            "entity_id": subject_id,
            "facts": [
                f["fact_key"]
                for f in asm.facts
                if f"{f['subject']['entity']}/{f['subject']['id']}" == anchor
            ],
        }
    if kind == "projection_run":
        return next(r for r in asm.projection_runs if r["run_id"] == anchor)
    if kind == "diagnosis_run":
        run = next(r for r in asm.diagnosis_runs if r["run_id"] == anchor)
        return dict(run) | {
            "indexed_proposals": [
                p["proposal_id"]
                for p in asm.proposals
                if p["diagnosis_run_id"] == anchor
            ],
            "unindexed_proposals": [
                p["proposal_id"]
                for p in asm.unindexed_proposals
                if p["diagnosis_run_id"] == anchor
            ],
        }
    if kind == "proposal":
        return next(
            p
            for p in asm.proposals + asm.unindexed_proposals
            if p["proposal_id"] == anchor
        )
    if kind == "decision":
        return next(
            d
            for p in asm.proposals
            for d in p["decisions"]
            if d["decision_id"] == anchor
        )
    if kind == "execution":
        attempts = [
            a
            for p in asm.proposals
            for a in p["executions"]
            if anchor in (a["execution_id"], a["idempotency_key"])
        ]
        return {
            "execution_id": attempts[0]["execution_id"] if attempts else anchor,
            "idempotency_key": (
                attempts[0]["idempotency_key"] if attempts else None
            ),
            "proposal_id": attempts[0]["proposal_id"] if attempts else None,
            "attempts": attempts,
        }
    if kind == "evaluation_run":
        return next(r for r in asm.evaluation_runs if r["run_id"] == anchor)
    if kind == "journal_only":
        return {
            "journal_events": [
                e for e in asm.journal if anchor in _deep_strings(e.get("data"))
            ],
            "note": (
                "this id appears only in the trace journal — the artifact "
                "was never persisted (e.g. a run that failed before "
                "freezing)"
            ),
        }
    return None


def _anchor_subjects(
    asm: TraceAssembly, anchor: str, kind: str, record: dict[str, Any] | None
) -> list[str]:
    """Subjects an operator can pivot to with ``--subject``."""
    subjects: set[str] = set()
    if kind == "fact" and record:
        subjects.add(
            f"{record['subject']['entity']}/{record['subject']['id']}"
        )
    elif kind == "entity":
        subjects.add(anchor)
    elif kind == "projection_run" and record:
        for candidate in record.get("candidates") or []:
            subject = candidate.get("subject") or {}
            if subject.get("entity"):
                subjects.add(f"{subject['entity']}/{subject.get('id')}")
        for entry in record.get("inputs") or []:
            if record.get("subject"):
                subjects.add(f"{record['subject']}/{entry.get('subject_id')}")
    elif kind == "diagnosis_run" and record:
        for p in asm.proposals + asm.unindexed_proposals:
            if p["diagnosis_run_id"] != anchor:
                continue
            if p.get("subject_id"):
                subjects.add(f"{p['subject_entity']}/{p['subject_id']}")
        projection = next(
            (r for r in asm.projection_runs if r["run_id"] == record.get("projection_run_id")),
            None,
        )
        if projection:
            for candidate in projection.get("candidates") or []:
                subject = candidate.get("subject") or {}
                if subject.get("entity"):
                    subjects.add(f"{subject['entity']}/{subject.get('id')}")
    elif kind == "proposal" and record:
        if record.get("subject_id"):
            subjects.add(f"{record['subject_entity']}/{record['subject_id']}")
    elif kind == "execution" and record and record.get("proposal_id"):
        proposal = next(
            (
                p
                for p in asm.proposals + asm.unindexed_proposals
                if p["proposal_id"] == record["proposal_id"]
            ),
            None,
        )
        if proposal and proposal.get("subject_id"):
            subjects.add(
                f"{proposal['subject_entity']}/{proposal['subject_id']}"
            )
    elif kind == "evaluation_run" and record:
        report = record.get("report") or {}
        subject_entity = (report.get("interval") or {}).get("subject")
        for entry in report.get("inputs") or []:
            if subject_entity:
                subjects.add(f"{subject_entity}/{entry.get('subject_id')}")
    return sorted(subjects)


def _record_touches(record: dict[str, Any], entity: str, subject_id: str) -> bool:
    for resolved in record.get("entities") or []:
        if resolved.get("entity") == entity and resolved.get("id") == subject_id:
            return True
    for event in record.get("events") or []:
        subject = event.get("subject") or {}
        if subject.get("entity") == entity and subject.get("id") == subject_id:
            return True
    return False


def _proposal_touches(
    proposal: dict[str, Any], entity: str, subject_id: str
) -> bool:
    if (
        proposal.get("subject_entity") == entity
        and proposal.get("subject_id") == subject_id
    ):
        return True
    for related in proposal.get("related_entities") or []:
        if related.get("entity") == entity and related.get("id") == subject_id:
            return True
    # Unindexed proposals carry no resolved subject; fall back to the
    # recorded params — the id is an opaque string appearing as a param value.
    params = proposal.get("params") or {}
    if not proposal.get("subject_id") and subject_id in params.values():
        return True
    return False


def _event_names_subject(
    event: dict[str, Any], entity: str, subject_id: str
) -> bool:
    data = event.get("data")
    for subject in _deep_subjects(data):
        if subject.get("entity") == entity and subject.get("id") == subject_id:
            return True
    return False


def _deep_subjects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        out = []
        if "entity" in value and "id" in value:
            out.append(value)
        for item in value.values():
            out.extend(_deep_subjects(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_deep_subjects(item))
        return out
    return []


def _subject_outcome(report: dict[str, Any], subject_id: str) -> dict[str, Any]:
    """One subject's disposition and pairing inside a frozen report."""
    entry = next(
        (
            e
            for e in report.get("inputs") or []
            if e.get("subject_id") == subject_id
        ),
        {},
    )
    outcome: dict[str, Any] = {
        "disposition": entry.get("disposition"),
        "cohort": entry.get("cohort"),
    }
    cohorts = report.get("cohorts") or {}
    for cohort_name, cohort in cohorts.items():
        for group in ("paired", "pending", "invalid", "ambiguous"):
            for item in cohort.get(group) or []:
                if item.get("subject_id") == subject_id:
                    outcome["detail"] = item
                    outcome.setdefault("cohort", cohort_name)
    return outcome


def _entity_state(
    asm: TraceAssembly, entity: str, subject_id: str
) -> dict[str, Any] | None:
    return next(
        (
            e
            for e in asm.entities
            if e["entity_type"] == entity and e["entity_id"] == subject_id
        ),
        None,
    )


def _count_by(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


# ------------------------------------------------------------------ export


def export_bundle(asm: TraceAssembly) -> dict[str, Any]:
    """A self-contained JSON-serializable trace export.

    Embeds the frozen run records, the observation evidence, and the raw
    snapshot bytes (as text — ingestion only ever stores UTF-8 sources),
    so the bundle still shows the historical inputs after the source
    files or workspace definitions change.
    """
    snapshots: list[dict[str, Any]] = []
    missing: list[tuple[str, str]] = []
    for snapshot in asm.snapshots:
        entry = dict(snapshot)
        try:
            content = Path(snapshot["path"]).read_bytes()
        except OSError as exc:
            entry["content_error"] = str(exc.strerror or exc)
            missing.append((snapshot["name"], entry["content_error"]))
        else:
            try:
                entry["content"] = content.decode("utf-8")
            except UnicodeDecodeError:
                import base64

                entry["content_base64"] = base64.b64encode(content).decode(
                    "ascii"
                )
        snapshots.append(entry)
    # A snapshot that went unreadable after assembly leaves the bundle
    # without its source evidence; flag that in the exported diagnostics
    # (and the caller's view of them) rather than reporting silent success.
    for name, error in missing:
        diagnostic = Diagnostic(
            "warning",
            "TRACE.EXPORT_SNAPSHOT_MISSING",
            f"snapshot {name} could not be embedded in the export: {error}",
        )
        if diagnostic not in asm.diagnostics:
            asm.diagnostics.append(diagnostic)
    return {
        "format": TRACE_FORMAT,
        "format_version": TRACE_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "workspace": asm.workspace,
        "journal": asm.journal,
        "journal_errors": asm.journal_errors,
        "observations": asm.observations,
        "observation_errors": asm.observation_errors,
        "snapshots": snapshots,
        "entities": asm.entities,
        "facts": asm.facts,
        "projection_runs": asm.projection_runs,
        "diagnosis_runs": asm.diagnosis_runs,
        "proposals": asm.proposals,
        "unindexed_proposals": asm.unindexed_proposals,
        "evaluation_runs": asm.evaluation_runs,
        "links": asm.links,
        "unresolved": asm.unresolved,
        "diagnostics": [d.to_dict() for d in asm.diagnostics],
    }


def write_export(asm: TraceAssembly, destination: str | Path) -> Path:
    """Write the export bundle to ``destination`` atomically.

    Refuses to overwrite: an export is point-in-time evidence and a silent
    rewrite would destroy an earlier one. Raises ``FileExistsError`` /
    ``OSError``; the caller renders them as diagnostics.
    """
    path = Path(destination)
    if path.exists():
        raise FileExistsError(f"export target already exists: {path}")
    payload = json.dumps(export_bundle(asm), indent=2, sort_keys=True)
    _atomic_write(path, payload.encode("utf-8"))
    return path


__all__ = [
    "NATURE_AI",
    "NATURE_DERIVED",
    "NATURE_EVALUATED",
    "NATURE_HUMAN",
    "NATURE_OBSERVED",
    "NATURE_WRITEBACK",
    "TRACE_FORMAT",
    "TRACE_FORMAT_VERSION",
    "TraceAssembly",
    "assemble_trace",
    "export_bundle",
    "trace_anchor",
    "trace_overview",
    "trace_subject",
    "write_export",
]
