from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from etlir.adapters.sources.exampleflow import ExampleFlowSourceAdapter
from etlir.adapters.targets.duckdb import DuckDBTargetAdapter
from etlir.fidelity import assess_fidelity
from etlir.types import RunStatus, TargetReference


def test_complete_translation_passes_accounting(repository_root: Path) -> None:
    adaptation = ExampleFlowSourceAdapter().adapt(
        repository_root / "examples/retail-orders/source.exampleflow.json"
    )
    assert adaptation.model is not None
    translation = DuckDBTargetAdapter().translate(adaptation.model)

    assessment = assess_fidelity(adaptation, adaptation.model, translation)

    assert assessment.status == RunStatus.COMPLETE
    assert assessment.summary["preserved"] == 4
    assert assessment.diagnostics == ()


def test_missing_target_record_becomes_blocking_diagnostic(repository_root: Path) -> None:
    adaptation = ExampleFlowSourceAdapter().adapt(
        repository_root / "examples/retail-orders/source.exampleflow.json"
    )
    assert adaptation.model is not None
    complete = DuckDBTargetAdapter().translate(adaptation.model)
    broken = replace(complete, records=complete.records[:-1])

    assessment = assess_fidelity(adaptation, adaptation.model, broken)

    assert assessment.status == RunStatus.BLOCKED
    assert "FIDELITY.UNACCOUNTED_MODEL" in {item.code for item in assessment.diagnostics}


def test_missing_source_mapping_becomes_blocking_diagnostic(repository_root: Path) -> None:
    adaptation = ExampleFlowSourceAdapter().adapt(
        repository_root / "examples/retail-orders/source.exampleflow.json"
    )
    assert adaptation.model is not None
    adaptation.model["pipeline"]["operations"][1]["source_refs"] = [
        {"artifact_id": "source", "pointer": "/pipeline/steps/999"}
    ]
    translation = DuckDBTargetAdapter().translate(adaptation.model)

    assessment = assess_fidelity(adaptation, adaptation.model, translation)

    assert assessment.status == RunStatus.BLOCKED
    assert "FIDELITY.UNACCOUNTED_SOURCE" in {item.code for item in assessment.diagnostics}


def test_preserved_record_must_reference_returned_artifact(repository_root: Path) -> None:
    adaptation = ExampleFlowSourceAdapter().adapt(
        repository_root / "examples/retail-orders/source.exampleflow.json"
    )
    assert adaptation.model is not None
    complete = DuckDBTargetAdapter().translate(adaptation.model)
    invalid_record = replace(
        complete.records[0],
        target_refs=(TargetReference("target/missing.sql", "cte:read_orders"),),
    )
    broken = replace(complete, records=(invalid_record, *complete.records[1:]))

    assessment = assess_fidelity(adaptation, adaptation.model, broken)

    assert assessment.status == RunStatus.BLOCKED
    assert "FIDELITY.UNKNOWN_TARGET_REFERENCE" in {
        item.code for item in assessment.diagnostics
    }


def test_unsupported_record_requires_linked_diagnostic(repository_root: Path) -> None:
    adaptation = ExampleFlowSourceAdapter().adapt(
        repository_root / "examples/unsupported-external-operation/source.exampleflow.json"
    )
    assert adaptation.model is not None
    complete = DuckDBTargetAdapter().translate(adaptation.model)
    unsupported_index = next(
        index
        for index, record in enumerate(complete.records)
        if record.operation_id == "fetch_loyalty_tier"
    )
    records = list(complete.records)
    records[unsupported_index] = replace(records[unsupported_index], diagnostic_ids=())
    broken = replace(complete, records=tuple(records))

    assessment = assess_fidelity(adaptation, adaptation.model, broken)

    assert assessment.status == RunStatus.BLOCKED
    assert "FIDELITY.UNSUPPORTED_WITHOUT_DIAGNOSTIC" in {
        item.code for item in assessment.diagnostics
    }
