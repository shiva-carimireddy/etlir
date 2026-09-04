"""Structural and semantic validation for Pipeline Semantic Model documents."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Iterable
from typing import Any

from etlir.diagnostics import Diagnostic, make_diagnostic
from etlir.schema import iter_validation_errors, json_pointer, load_spec_schema
from etlir.types import JsonObject


def validate_model(model: JsonObject) -> tuple[Diagnostic, ...]:
    """Validate the model schema followed by cross-reference invariants."""

    diagnostics: list[Diagnostic] = []
    schema = load_spec_schema("pipeline-model.schema.json")
    schema_errors = iter_validation_errors(model, schema)
    for error in schema_errors:
        pointer = json_pointer(list(error.absolute_path))
        diagnostics.append(
            make_diagnostic(
                code="MODEL.SCHEMA_INVALID",
                severity="error",
                category="invalid",
                stage="model_validation",
                message=error.message,
                artifact="pipeline-model.json",
                pointer=pointer,
                remediation="Correct the model or the Source Adapter mapping before translation.",
            )
        )

    if schema_errors:
        return tuple(diagnostics)

    pipeline = _object(model["pipeline"])
    datasets = _objects(pipeline["datasets"])
    operations = _objects(pipeline["operations"])
    parameters = _objects(pipeline["parameters"])
    edges = _objects(pipeline["edges"])

    diagnostics.extend(_duplicate_id_diagnostics("datasets", datasets))
    diagnostics.extend(_duplicate_id_diagnostics("operations", operations))
    diagnostics.extend(_duplicate_id_diagnostics("parameters", parameters))

    dataset_ids = {str(dataset["id"]) for dataset in datasets}
    operation_ids = {str(operation["id"]) for operation in operations}
    parameter_ids = {str(parameter["id"]) for parameter in parameters}

    for index, operation in enumerate(operations):
        operation_id = str(operation["id"])
        if operation["kind"] in {"read", "write"}:
            dataset_id = str(operation["dataset_id"])
            if dataset_id not in dataset_ids:
                diagnostics.append(
                    _invalid_reference(
                        pointer=f"/pipeline/operations/{index}/dataset_id",
                        operation_id=operation_id,
                        message=f"Dataset reference '{dataset_id}' does not exist.",
                    )
                )
        for expression_pointer, expression in _operation_expressions(index, operation):
            for parameter_name in _parameter_references(expression):
                if parameter_name not in parameter_ids:
                    diagnostics.append(
                        _invalid_reference(
                            pointer=expression_pointer,
                            operation_id=operation_id,
                            message=f"Parameter reference '{parameter_name}' does not exist.",
                        )
                    )

    edge_pairs: list[tuple[str, str]] = []
    for index, edge in enumerate(edges):
        source = str(edge["from"])
        target = str(edge["to"])
        edge_pairs.append((source, target))
        for endpoint, value in (("from", source), ("to", target)):
            if value not in operation_ids:
                diagnostics.append(
                    _invalid_reference(
                        pointer=f"/pipeline/edges/{index}/{endpoint}",
                        message=f"Operation reference '{value}' does not exist.",
                    )
                )

    for pair, count in Counter(edge_pairs).items():
        if count > 1:
            diagnostics.append(
                make_diagnostic(
                    code="MODEL.DUPLICATE_EDGE",
                    severity="error",
                    category="invalid",
                    stage="model_validation",
                    message=f"Edge {pair[0]} -> {pair[1]} occurs {count} times.",
                    artifact="pipeline-model.json",
                    pointer="/pipeline/edges",
                    remediation="Remove duplicate edges from the model.",
                )
            )

    if not any(diagnostic.code == "MODEL.INVALID_REFERENCE" for diagnostic in diagnostics):
        cycle = _find_cycle(operation_ids, edge_pairs)
        if cycle:
            diagnostics.append(
                make_diagnostic(
                    code="MODEL.CYCLE",
                    severity="error",
                    category="invalid",
                    stage="model_validation",
                    message="The operation graph contains a cycle.",
                    artifact="pipeline-model.json",
                    pointer="/pipeline/edges",
                    details={"operation_ids": cycle},
                    remediation=(
                        "Remove the cycle or represent iterative behavior explicitly in a "
                        "future model version."
                    ),
                )
            )

    return tuple(diagnostics)


def _duplicate_id_diagnostics(collection: str, values: list[JsonObject]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    counts = Counter(str(value["id"]) for value in values)
    for identifier, count in sorted(counts.items()):
        if count > 1:
            diagnostics.append(
                make_diagnostic(
                    code="MODEL.DUPLICATE_ID",
                    severity="error",
                    category="invalid",
                    stage="model_validation",
                    message=f"Identifier '{identifier}' occurs {count} times in {collection}.",
                    artifact="pipeline-model.json",
                    pointer=f"/pipeline/{collection}",
                    remediation=f"Use a unique ID for every item in {collection}.",
                )
            )
    return diagnostics


def _invalid_reference(
    *,
    pointer: str,
    message: str,
    operation_id: str | None = None,
) -> Diagnostic:
    return make_diagnostic(
        code="MODEL.INVALID_REFERENCE",
        severity="error",
        category="invalid",
        stage="model_validation",
        message=message,
        artifact="pipeline-model.json",
        pointer=pointer,
        operation_id=operation_id,
        remediation="Correct the reference in the Source Adapter or source artifact.",
    )


def _operation_expressions(
    operation_index: int,
    operation: JsonObject,
) -> Iterable[tuple[str, JsonObject]]:
    kind = operation["kind"]
    if kind == "filter":
        yield f"/pipeline/operations/{operation_index}/predicate", _object(operation["predicate"])
    if kind == "derive":
        for assignment_index, assignment in enumerate(_objects(operation["assignments"])):
            pointer = (
                f"/pipeline/operations/{operation_index}/assignments/"
                f"{assignment_index}/expression"
            )
            yield pointer, _object(assignment["expression"])


def _parameter_references(expression: JsonObject) -> Iterable[str]:
    if expression["kind"] == "parameter":
        yield str(expression["name"])
    if expression["kind"] == "binary":
        yield from _parameter_references(_object(expression["left"]))
        yield from _parameter_references(_object(expression["right"]))


def _find_cycle(operation_ids: set[str], edges: list[tuple[str, str]]) -> list[str]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {identifier: 0 for identifier in operation_ids}
    for source, target in edges:
        adjacency[source].append(target)
        indegree[target] += 1

    queue = deque(sorted(identifier for identifier, degree in indegree.items() if degree == 0))
    visited: list[str] = []
    while queue:
        current = queue.popleft()
        visited.append(current)
        for target in sorted(adjacency[current]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    if len(visited) == len(operation_ids):
        return []
    return sorted(identifier for identifier, degree in indegree.items() if degree > 0)


def _object(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object after successful schema validation.")
    return value


def _objects(value: Any) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError("Expected a list of JSON objects after successful schema validation.")
    return value
