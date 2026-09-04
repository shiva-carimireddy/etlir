from __future__ import annotations

from pathlib import Path

from etlir.adapters.sources.exampleflow import ExampleFlowSourceAdapter
from etlir.adapters.targets.duckdb import DuckDBTargetAdapter
from etlir.types import Disposition


def test_duckdb_translates_supported_linear_flow(repository_root: Path) -> None:
    source = repository_root / "examples/retail-orders/source.exampleflow.json"
    model = ExampleFlowSourceAdapter().adapt(source).model
    assert model is not None

    result = DuckDBTargetAdapter().translate(model)

    assert result.diagnostics == ()
    assert len(result.artifacts) == 1
    sql = result.artifacts[0].content.decode("utf-8")
    assert '"status" = \'COMPLETE\'' in sql
    assert '"gross_amount" - "discount_amount"' in sql
    assert "'gross_amount': 'DOUBLE'" in sql
    assert "FORMAT PARQUET" in sql
    assert {record.disposition for record in result.records} == {Disposition.PRESERVED}


def test_duckdb_blocks_unsupported_external_operation(repository_root: Path) -> None:
    source = repository_root / "examples/unsupported-external-operation/source.exampleflow.json"
    model = ExampleFlowSourceAdapter().adapt(source).model
    assert model is not None

    result = DuckDBTargetAdapter().translate(model)

    assert result.artifacts == ()
    assert "TARGET.UNSUPPORTED_OPERATION" in {item.code for item in result.diagnostics}
    dispositions = {record.operation_id: record.disposition for record in result.records}
    assert dispositions["fetch_loyalty_tier"] == Disposition.UNSUPPORTED
    assert dispositions["read_orders"] == Disposition.BLOCKED
    assert dispositions["write_orders"] == Disposition.BLOCKED
