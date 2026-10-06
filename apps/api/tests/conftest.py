from __future__ import annotations

from pathlib import Path

import apisupport
import pytest


@pytest.fixture
def roots(tmp_path: Path) -> Path:
    root = tmp_path / "roots" / "workspaces"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def workspace(roots: Path) -> Path:
    return apisupport.copy_example(roots)
