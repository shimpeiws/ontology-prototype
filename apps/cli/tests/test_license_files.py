"""Every Python project ships the repository license text.

Each package directory is its own setuptools build root, so a wheel can only
include a license file that lives inside it. These copies must stay identical
to the repository-root LICENSE.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECTS = ("packages/core", "apps/cli", "apps/api")


@pytest.mark.parametrize("project", PROJECTS)
def test_project_license_matches_the_repository_license(project: str) -> None:
    root_license = (REPO_ROOT / "LICENSE").read_bytes()
    assert (REPO_ROOT / project / "LICENSE").read_bytes() == root_license
