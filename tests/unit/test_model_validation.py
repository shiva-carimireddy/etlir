from __future__ import annotations

import copy
from pathlib import Path

from jsonschema import Draft202012Validator

from etlir.adapters.sources.exampleflow import ExampleFlowSourceAdapter
from etlir.schema import SCHEMA_NAMES, load_spec_schema
from etlir.validation import validate_model


def test_normative_schemas_are_valid_draft_2020_12() -> None:
    for name in SCHEMA_NAMES:
        Draft202012Validator.check_schema(load_spec_schema(name))


def test_supported_example_is_model_valid(repository_root: Path) -> None:
    source = repository_root / "examples/retail-orders/source.exampleflow.json"
    model = ExampleFlowSourceAdapter().adapt(source).model

    assert model is not None
    assert validate_model(model) == ()


def test_duplicate_operation_id_is_explicit(repository_root: Path) -> None:
    source = repository_root / "examples/retail-orders/source.exampleflow.json"
    original = ExampleFlowSourceAdapter().adapt(source).model
    assert original is not None
    model = copy.deepcopy(original)
    model["pipeline"]["operations"][1]["id"] = "read_orders"

    diagnostics = validate_model(model)

    assert "MODEL.DUPLICATE_ID" in {item.code for item in diagnostics}


def test_missing_dataset_reference_is_explicit(repository_root: Path) -> None:
    source = repository_root / "examples/retail-orders/source.exampleflow.json"
    original = ExampleFlowSourceAdapter().adapt(source).model
    assert original is not None
    model = copy.deepcopy(original)
    model["pipeline"]["operations"][0]["dataset_id"] = "missing_dataset"

    diagnostics = validate_model(model)

    assert "MODEL.INVALID_REFERENCE" in {item.code for item in diagnostics}

