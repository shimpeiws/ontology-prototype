from __future__ import annotations

from pathlib import Path

import clisupport
import pytest


@pytest.fixture
def example_workspace(tmp_path: Path) -> Path:
    return clisupport.copy_example(tmp_path)
