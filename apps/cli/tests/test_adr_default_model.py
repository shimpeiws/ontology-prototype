"""The documented default model must match the code and the ADR.

`DEFAULT_MODEL` is the value the CLI uses, and `docs/diagnosis.md` names it for
readers. ADR 0002 records why that value was chosen. The three have to agree, or
the documentation tells the reader something the code does not do.
"""

from __future__ import annotations

import re
from pathlib import Path

from ontology_cli import providers

REPO_ROOT = Path(__file__).resolve().parents[3]
DIAGNOSIS_DOC = REPO_ROOT / "docs" / "diagnosis.md"
EVALUATION_DOC = REPO_ROOT / "docs" / "diagnosis-evaluation.md"
ADR = REPO_ROOT / "docs" / "adr" / "0002-default-diagnosis-model.md"
ADR_LINK = "adr/0002-default-diagnosis-model.md"

PROVISIONAL = "Pending evidence"


def _documented_default_paragraph() -> str:
    """The one paragraph of docs/diagnosis.md that names the default model."""
    text = DIAGNOSIS_DOC.read_text(encoding="utf-8")
    matches = [p for p in text.split("\n\n") if "documented default" in p]
    assert len(matches) == 1, (
        f"docs/diagnosis.md should name the documented default once, "
        f"found {len(matches)}"
    )
    return matches[0]


def _adr_status() -> str:
    match = re.search(
        r"^- Status: (.+)$", ADR.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match is not None, "the default model ADR has no Status line"
    return match.group(1).strip()


def test_documented_default_is_the_implemented_default() -> None:
    match = re.search(r"documented default `([^`]+)`", _documented_default_paragraph())
    assert match is not None, "docs/diagnosis.md no longer names a default model"
    assert match.group(1) == providers.DEFAULT_MODEL


def test_default_is_provisional_exactly_while_the_adr_is_pending() -> None:
    """A settled decision must not be described as provisional, and a pending
    one must not be described as settled."""
    provisional = "provisional" in _documented_default_paragraph()
    assert provisional == (_adr_status() == PROVISIONAL)


def test_the_adr_is_linked_from_the_pages_that_use_it() -> None:
    assert ADR_LINK in DIAGNOSIS_DOC.read_text(encoding="utf-8")
    assert ADR_LINK in EVALUATION_DOC.read_text(encoding="utf-8")


def test_the_adr_keeps_the_repository_format() -> None:
    text = ADR.read_text(encoding="utf-8")
    for heading in ("## Context", "## Decision", "## Alternatives", "## Consequences"):
        assert f"\n{heading}\n" in text, f"ADR 0002 is missing {heading}"
    for field in ("- Status: ", "- Date: ", "- Decision owner: "):
        assert f"\n{field}" in text, f"ADR 0002 is missing {field}"
