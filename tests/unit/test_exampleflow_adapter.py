from __future__ import annotations

from pathlib import Path

from etlir.adapters.sources.exampleflow import ExampleFlowSourceAdapter


def test_exampleflow_accounts_for_every_source_step(repository_root: Path) -> None:
    source = repository_root / "examples/retail-orders/source.exampleflow.json"

    result = ExampleFlowSourceAdapter().adapt(source)

    assert result.model is not None
    assert result.diagnostics == ()
    assert [element.element_id for element in result.source_elements] == [
        "read_orders",
        "keep_completed",
        "calculate_net",
        "write_orders",
    ]
    operations = result.model["pipeline"]["operations"]
    modeled_pointers = {
        reference["pointer"]
        for operation in operations
        for reference in operation["source_refs"]
    }
    assert modeled_pointers == {element.pointer for element in result.source_elements}


def test_exampleflow_preserves_external_operation(repository_root: Path) -> None:
    source = repository_root / "examples/unsupported-external-operation/source.exampleflow.json"

    result = ExampleFlowSourceAdapter().adapt(source)

    assert result.model is not None
    operations = result.model["pipeline"]["operations"]
    external = next(item for item in operations if item["id"] == "fetch_loyalty_tier")
    assert external["kind"] == "external_operation"
    assert external["operation_type"] == "http_request"
    assert external["configuration"]["method"] == "POST"


def test_invalid_source_returns_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "invalid.json"
    source.write_text('{"format": "exampleflow"}', encoding="utf-8")

    result = ExampleFlowSourceAdapter().adapt(source)

    assert result.model is None
    assert {item.code for item in result.diagnostics} == {"SOURCE.SCHEMA_INVALID"}

