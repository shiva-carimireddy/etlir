from __future__ import annotations

from datetime import datetime
from pathlib import Path

from etlir.engine import translate
from etlir.schema import iter_validation_errors, load_spec_schema


def test_evidence_matches_normative_schema(
    repository_root: Path,
    tmp_path: Path,
    fixed_time: datetime,
) -> None:
    result = translate(
        source_path=repository_root / "examples/retail-orders/source.exampleflow.json",
        output_directory=tmp_path / "output",
        source_adapter_name="exampleflow",
        target_adapter_name="duckdb",
        created_at=fixed_time,
    )

    errors = iter_validation_errors(
        result.evidence,
        load_spec_schema("evidence-bundle.schema.json"),
        include_spec_registry=True,
    )
    assert errors == []
    markdown = (tmp_path / "output/evidence.md").read_text(encoding="utf-8")
    assert "**Status:** `complete`" in markdown
    assert "No diagnostics." in markdown

