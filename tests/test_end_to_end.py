"""End-to-end: benchmark cases through conversion, execution, comparison and mutations."""

from __future__ import annotations

from pathlib import Path

import pytest

from etlir.benchmark import run_benchmark
from etlir.cli import main
from tests.conftest import CASES, requires_spark

CASE_DIRS = sorted(p for p in CASES.iterdir() if (p / "case.toml").exists())


def _assert_all_pass(results: dict) -> None:  # type: ignore[type-arg]
    for key, value in results["summary"].items():
        assert value["count"] == value["of"], (key, value, results["rows"])


def test_benchmark_with_a_relative_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CI passes a relative --out; seeded targets (keyed writes) must land where the job
    # runtime looks for them.
    monkeypatch.chdir(tmp_path)
    case = CASES / "pc-update-strategy"
    results = run_benchmark([case], Path("relative-out"), targets=["duckdb"], mutation_target=None)
    _assert_all_pass(results)


def test_benchmark_duckdb(tmp_path: Path) -> None:
    results = run_benchmark(CASE_DIRS, tmp_path, targets=["duckdb"])
    _assert_all_pass(results)
    mutations = results["summary"]["mutations_detected"]
    assert mutations["of"] >= 5


@requires_spark
@pytest.mark.spark
def test_benchmark_spark(tmp_path: Path) -> None:
    results = run_benchmark(CASE_DIRS, tmp_path, targets=["spark"], mutation_target=None)
    _assert_all_pass(results)


def test_cli_convert_writes_every_stage(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main(["convert", str(CASES / "pc-orders" / "source"), "--out", str(out)]) == 0
    for name in (
        "run_manifest.json",
        "inventory.json",
        "raw_ir.json",
        "canonical_ir.json",
        "validation.json",
        "evidence.json",
        "summary.json",
        "report.html",
        "targets/spark/workflow_plan.json",
        "targets/duckdb/workflow_plan.json",
    ):
        assert (out / name).is_file(), name
    assert main(["validate", str(out / "canonical_ir.json")]) == 0
    assert "ETLIR conversion report" in (out / "report.html").read_text("utf-8")


def test_support_matrix_page_matches_manifests(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["capabilities", "--markdown"]) == 0
    generated = capsys.readouterr().out.strip().splitlines()
    page = (CASES.parents[1] / "docs" / "support-matrix.md").read_text("utf-8")
    table = [line for line in page.splitlines() if line.startswith("|")]
    assert table == generated, "regenerate docs/support-matrix.md (see its header)"


def test_cli_inspect_and_capabilities(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", str(CASES / "pc-orders" / "source")]) == 0
    assert '"session_to_mapping"' in capsys.readouterr().out
    assert main(["capabilities", "--markdown"]) == 0
    assert "| `operation.join` | supported | supported |" in capsys.readouterr().out
    assert main(["convert", str(tmp_path / "missing"), "--out", str(tmp_path / "o")]) == 2
