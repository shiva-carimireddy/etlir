from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from etlir.cli import app
from etlir.engine import translate
from etlir.types import RunStatus


def test_unsupported_flow_is_visible_and_matches_golden(
    repository_root: Path,
    tmp_path: Path,
    fixed_time: datetime,
) -> None:
    output = tmp_path / "unsupported-output"

    result = translate(
        source_path=(
            repository_root
            / "examples/unsupported-external-operation/source.exampleflow.json"
        ),
        output_directory=output,
        source_adapter_name="exampleflow",
        target_adapter_name="duckdb",
        created_at=fixed_time,
    )

    assert result.status == RunStatus.BLOCKED
    assert result.exit_code == 2
    assert not (output / "target/pipeline.sql").exists()
    model = json.loads((output / "pipeline-model.json").read_text(encoding="utf-8"))
    assert any(item["kind"] == "external_operation" for item in model["pipeline"]["operations"])

    expected = repository_root / "examples/unsupported-external-operation/expected"
    for name in ("evidence.json", "evidence.md"):
        assert (output / name).read_bytes() == (expected / name).read_bytes()


def test_unsupported_flow_exits_two_through_cli(repository_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "cli-unsupported"
    result = CliRunner().invoke(
        app,
        [
            "translate",
            "--source",
            "exampleflow",
            "--target",
            "duckdb",
            str(
                repository_root
                / "examples/unsupported-external-operation/source.exampleflow.json"
            ),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 2
    assert "Status: blocked" in result.output
    assert "TARGET.UNSUPPORTED_OPERATION" in result.output
    assert not (output / "target/pipeline.sql").exists()

