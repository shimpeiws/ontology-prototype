from __future__ import annotations

from pathlib import Path

import pytest
import support


@pytest.fixture
def valid_workspace(tmp_path: Path) -> Path:
    return support.make_workspace(tmp_path)
