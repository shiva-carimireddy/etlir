from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from etlir.canonical.model import CanonicalDocument
from etlir.sources.powercenter import PowerCenterAdapter
from tests.fixtures.plugins import ToyAdapter

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[1]
CASES = REPO / "benchmarks" / "cases"


@pytest.fixture
def toy_inputs() -> tuple[list[Path], Path]:
    root = FIXTURES / "toy"
    return sorted(root.glob("*.toy.json")), root


@pytest.fixture
def toy_doc(toy_inputs: tuple[list[Path], Path]) -> CanonicalDocument:
    inputs, root = toy_inputs
    adapter = ToyAdapter()
    return adapter.normalize(adapter.load(inputs, root)).document


def case_doc(case: str, *files: str) -> CanonicalDocument:
    src = CASES / case / "source"
    paths = [src / f for f in files] if files else sorted(src.iterdir())
    adapter = PowerCenterAdapter()
    return adapter.normalize(adapter.load(paths, src)).document


@pytest.fixture(scope="session")
def orders_doc() -> CanonicalDocument:
    return case_doc("pc-orders")


@pytest.fixture(scope="session")
def mixed_doc() -> CanonicalDocument:
    return case_doc("pc-mixed-blocked")


def spark_available() -> bool:
    """PySpark importable and a Java 17+ runtime reachable (JAVA_HOME or PATH)."""
    try:
        import pyspark  # noqa: F401
    except ImportError:
        return False
    home = os.environ.get("JAVA_HOME")
    java = str(Path(home) / "bin" / "java") if home else shutil.which("java")
    if not java:
        return False
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, check=False)
    except OSError:
        return False
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not m:
        return False
    major = int(m.group(1)) if m.group(1) != "1" else int(m.group(2) or 0)
    return major >= 17


requires_spark = pytest.mark.skipif(
    not spark_available(), reason="needs pyspark and Java 17+ (set JAVA_HOME)"
)
