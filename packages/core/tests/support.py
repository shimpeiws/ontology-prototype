from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WORKSPACE = REPO_ROOT / "examples" / "job-invoicing"

DOMAIN = {
    "schema_version": 1,
    "kind": "domain",
    "domain": {
        "name": "test-domain",
        "policies": {
            "entity_id_type": "string",
            "event_times": {
                "occurrence_field": "occurred_at",
                "observation_field": "observed_at",
                "date_only_timezone": "UTC",
            },
        },
        "entities": {
            "Job": {
                "properties": {
                    "status": {"type": "string", "required": True},
                    "assignee": {"type": "ref", "ref": "Person"},
                    "amount": {"type": "number"},
                }
            },
            "Invoice": {
                "properties": {
                    "job": {"type": "ref", "ref": "Job"},
                }
            },
            "Person": {
                "properties": {
                    "name": {"type": "string"},
                }
            },
        },
        "events": {
            "JobCompleted": {"subject": "Job"},
            "InvoiceIssued": {"subject": "Job"},
        },
        "projections": {
            "CompletedButNotInvoiced": {
                "subject": "Job",
                "select": {
                    "required_events": ["JobCompleted"],
                    "absent_events": ["InvoiceIssued"],
                    "elapsed": {"comparator": "gt", "value": 2, "unit": "days"},
                },
            },
        },
    },
}

MAPPING = {
    "schema_version": 1,
    "kind": "mapping",
    "mapping": {
        "name": "jobs-csv",
        "source": {"name": "jobs", "format": "csv"},
        "entities": [
            {
                "entity": "Job",
                "id": {"field": "id"},
                "properties": {
                    "status": {"field": "status"},
                    "assignee": {"field": "assignee"},
                    "amount": {"field": "amount", "as": "number"},
                },
            },
            {
                "entity": "Person",
                "id": {"field": "assignee"},
                "properties": {"name": {"field": "assignee"}},
            },
        ],
        "events": [
            {
                "event": "JobCompleted",
                "subject": {"entity": "Job", "id": {"field": "id"}},
                "when": {"op": "eq", "field": "status", "value": "completed"},
                "occurred_at": {"field": "completed_at", "as": "date"},
            },
            {
                "event": "InvoiceIssued",
                "subject": {"entity": "Job", "id": {"field": "id"}},
                "when": {"op": "not_null", "field": "invoiced_at"},
                "occurred_at": {"field": "invoiced_at", "as": "date"},
            },
        ],
    },
}

EVALUATION = {
    "schema_version": 1,
    "kind": "evaluation",
    "evaluation": {
        "name": "completion-to-invoice-latency",
        "interval": {"start_event": "JobCompleted", "end_event": "InvoiceIssued"},
        "metric": "duration",
        "aggregation": "median",
        "unit": "days",
        "baseline": {"value": 9},
        "target": {"comparator": "lt", "value": 4},
        "guardrails": [
            {
                "name": "invoice-error-rate",
                "metric": "invoice_error_rate",
                "comparator": "not_increase",
            }
        ],
    },
}

AGENT = {
    "schema_version": 1,
    "kind": "agent",
    "agent": {
        "name": "billing-delay-diagnosis",
        "projection": "CompletedButNotInvoiced",
        "task": {
            "goal": "Identify likely causes of delayed invoicing.",
            "deliverables": [
                "explanation",
                "hypotheses",
                "follow_up_questions",
                "action_proposals",
            ],
        },
        "permitted_actions": {
            "SendReminder": {
                "params": {
                    "target": {"type": "ref", "ref": "Person", "required": True},
                    "related_entity": {"type": "ref", "ref": "Job", "required": True},
                    "reason": {"type": "string", "required": True},
                }
            }
        },
    },
}

FILES = ("domain", "mapping", "evaluation", "agent")
BASE_DOCS = {"domain": DOMAIN, "mapping": MAPPING, "evaluation": EVALUATION, "agent": AGENT}


def base_docs() -> dict[str, dict]:
    return copy.deepcopy(BASE_DOCS)


def make_workspace(tmp_path: Path, docs: dict[str, dict | str] | None = None) -> Path:
    docs = docs if docs is not None else base_docs()
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        doc = docs[name]
        text = doc if isinstance(doc, str) else yaml.safe_dump(doc, sort_keys=False)
        (tmp_path / f"{name}.yaml").write_text(text, encoding="utf-8")
    return tmp_path


@pytest.fixture
def valid_workspace(tmp_path: Path) -> Path:
    return make_workspace(tmp_path)


def codes(result) -> list[str]:
    return [d.code for d in result.diagnostics]
