from __future__ import annotations

import pytest
from pydantic import ValidationError

from etlir.canonical.invariants import validate
from etlir.canonical.model import (
    CanonicalDocument,
    DataEdge,
    DataType,
    Dependency,
    Operation,
    Parameter,
    ParameterScope,
    Pipeline,
    SourceRef,
    Task,
    TaskKind,
    TypeKind,
    UnsupportedOp,
)
from etlir.serialization import dumps

SRC = SourceRef(adapter="test", artifact="a", locator="/")


def codes(doc: CanonicalDocument) -> list[str]:
    return [d.code for d in validate(doc)]


def test_valid_document_has_no_findings(toy_doc: CanonicalDocument) -> None:
    assert validate(toy_doc) == []


def test_json_round_trip_is_exact(toy_doc: CanonicalDocument) -> None:
    assert CanonicalDocument.model_validate_json(dumps(toy_doc)) == toy_doc


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceRef.model_validate({"adapter": "a", "artifact": "b", "locator": "/", "x": 1})


def test_entities_require_source_trace() -> None:
    with pytest.raises(ValidationError):
        Task.model_validate({"id": "t", "name": "t", "kind": "command"})


def test_duplicate_identifier(toy_doc: CanonicalDocument) -> None:
    doc = toy_doc.model_copy(update={"datasets": toy_doc.datasets + toy_doc.datasets[:1]})
    assert "IR-V-001" in codes(doc)


def test_unknown_dataset_reference(toy_doc: CanonicalDocument) -> None:
    doc = toy_doc.model_copy(update={"datasets": toy_doc.datasets[1:]})
    assert "IR-V-002" in codes(doc)


def test_task_cycle() -> None:
    a = Task(
        id="a", name="a", kind=TaskKind.COMMAND, depends_on=[Dependency(task_id="b")], source=SRC
    )
    b = Task(
        id="b", name="b", kind=TaskKind.COMMAND, depends_on=[Dependency(task_id="a")], source=SRC
    )
    doc = CanonicalDocument(pipelines=[Pipeline(id="p", name="p", tasks=[a, b], source=SRC)])
    assert "IR-V-003" in codes(doc)


def test_dataflow_cycle_and_read_with_input(toy_doc: CanonicalDocument) -> None:
    df = toy_doc.dataflows[0]
    back = DataEdge(from_operation=df.operations[2].id, to_operation=df.operations[0].id)
    doc = toy_doc.model_copy(
        update={"dataflows": [df.model_copy(update={"edges": [*df.edges, back]})]}
    )
    found = codes(doc)
    assert "IR-V-004" in found
    assert "IR-V-007" in found


def test_sensitive_parameter_default_is_rejected() -> None:
    p = Parameter(
        id="p",
        name="PWD",
        scope=ParameterScope.GLOBAL,
        type=DataType(kind=TypeKind.STRING),
        default="hunter2",
        sensitive=True,
        source=SRC,
    )
    assert codes(CanonicalDocument(parameters=[p])) == ["IR-V-009"]


def test_unsupported_operation_stays_visible(toy_doc: CanonicalDocument) -> None:
    df = toy_doc.dataflows[0]
    ops = [
        *df.operations,
        Operation(id="op.x", spec=UnsupportedOp(native_kind="Custom", reason="r"), source=SRC),
    ]
    doc = toy_doc.model_copy(update={"dataflows": [df.model_copy(update={"operations": ops})]})
    diags = validate(doc)
    assert [d.code for d in diags] == ["IR-V-010"]
    assert diags[0].severity.value == "warning"
    assert diags[0].source == SRC
