from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def fixed_time() -> datetime:
    return datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
