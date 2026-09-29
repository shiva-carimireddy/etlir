from __future__ import annotations

from pathlib import Path

import pytest

from etlir.canonical.model import CanonicalDocument
from tests.fixtures.plugins import ToyAdapter

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def toy_inputs() -> tuple[list[Path], Path]:
    root = FIXTURES / "toy"
    return sorted(root.glob("*.toy.json")), root


@pytest.fixture
def toy_doc(toy_inputs: tuple[list[Path], Path]) -> CanonicalDocument:
    inputs, root = toy_inputs
    adapter = ToyAdapter()
    return adapter.normalize(adapter.load(inputs, root)).document
