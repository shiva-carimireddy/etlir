from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import duckdb
from typer.testing import CliRunner

from etlir.cli import app
from etlir.engine import translate
from etlir.types import RunStatus


def test_supported_flow_matches_golden_and_executes(
    repository_root: Path,
    tmp_path: Path,
    fixed_time: datetime,
    monkeypatch,
) -> None:
    example = tmp_path / "retail-orders"
    shutil.copytree(repository_root / "examples/retail-orders", example)
    output = tmp_path / "evidence-output"

    result = translate(
        source_path=example / "source.exampleflow.json",
        output_directory=output,
        source_adapter_name="exampleflow",
        target_adapter_name="duckdb",
        created_at=fixed_time,
    )

    assert result.status == RunStatus.COMPLETE
    expected = repository_root / "examples/retail-orders/expected"
    for name in (
        "pipeline-model.json",
        "target/pipeline.sql",
        "evidence.json",
        "evidence.md",
    ):
        assert (output / name).read_bytes() == (expected / name).read_bytes()

    (example / "build").mkdir()
    monkeypatch.chdir(example)
    connection = duckdb.connect()
    connection.execute((output / "target/pipeline.sql").read_text(encoding="utf-8"))
    rows = connection.execute(
        "SELECT order_id, net_amount FROM read_parquet('build/orders.parquet') ORDER BY order_id"
    ).fetchall()
    assert rows == [(1001, 110.0), (1003, 45.0)]


def test_supported_flow_runs_through_cli(repository_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "cli-output"
    result = CliRunner().invoke(
        app,
        [
            "translate",
            "--source",
            "exampleflow",
            "--target",
            "duckdb",
            str(repository_root / "examples/retail-orders/source.exampleflow.json"),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Status: complete" in result.output
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["summary"]["preserved"] == 4
