"""DuckDB SQL Target Adapter for the v0.1 reference slice."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from etlir.diagnostics import Diagnostic, make_diagnostic
from etlir.types import (
    AdapterDescriptor,
    AdapterKind,
    Disposition,
    GeneratedArtifact,
    JsonObject,
    TargetReference,
    TranslationRecord,
    TranslationResult,
)

TARGET_PATH = "target/pipeline.sql"


class DuckDBTargetAdapter:
    """Translate a linear relational model subset into DuckDB SQL."""

    descriptor = AdapterDescriptor(
        name="duckdb",
        kind=AdapterKind.TARGET,
        version="1.0.0",
        supported_model_versions=("0.1.0",),
    )

    def translate(self, model: JsonObject) -> TranslationResult:
        pipeline = _object(model["pipeline"])
        operations = _objects(pipeline["operations"])
        datasets = {str(item["id"]): item for item in _objects(pipeline["datasets"])}
        edges = _objects(pipeline["edges"])

        diagnostics: list[Diagnostic] = []
        diagnostics.extend(self._capability_diagnostics(model, operations, datasets, edges))
        if diagnostics:
            return TranslationResult(
                descriptor=self.descriptor,
                artifacts=(),
                records=self._blocked_records(operations, diagnostics),
                diagnostics=tuple(diagnostics),
            )

        ordered = _topological_order(operations, edges)
        predecessors = _predecessors(edges)
        ctes: list[str] = []
        records_by_id: dict[str, TranslationRecord] = {}
        write_operation: JsonObject | None = None

        for operation in ordered:
            operation_id = str(operation["id"])
            operation_index = operations.index(operation)
            kind = str(operation["kind"])
            source_refs = tuple(_objects(operation["source_refs"]))

            if kind == "read":
                dataset = datasets[str(operation["dataset_id"])]
                path = str(_object(dataset["location"])["uri"])
                columns = _render_csv_columns(_objects(dataset["fields"]))
                ctes.append(
                    f"{_quote_identifier(operation_id)} AS (\n"
                    "    SELECT *\n"
                    f"    FROM read_csv({_quote_literal(path)},\n"
                    "      header = true,\n"
                    f"      columns = {columns})\n"
                    "  )"
                )
                basis = "CSV read and declared field types translated to DuckDB read_csv."
                locator = f"cte:{operation_id}"
            elif kind == "filter":
                input_id = predecessors[operation_id][0]
                predicate = _render_expression(_object(operation["predicate"]))
                ctes.append(
                    f"{_quote_identifier(operation_id)} AS (\n"
                    "    SELECT *\n"
                    f"    FROM {_quote_identifier(input_id)}\n"
                    f"    WHERE {predicate}\n"
                    "  )"
                )
                basis = "Filter expression translated to a DuckDB WHERE predicate."
                locator = f"cte:{operation_id}"
            elif kind == "derive":
                input_id = predecessors[operation_id][0]
                assignments = _objects(operation["assignments"])
                rendered_assignments = [
                    f"{_render_expression(_object(item['expression']))} "
                    f"AS {_quote_identifier(str(item['field']))}"
                    for item in assignments
                ]
                select_additions = ",\n      ".join(rendered_assignments)
                ctes.append(
                    f"{_quote_identifier(operation_id)} AS (\n"
                    "    SELECT\n"
                    "      *,\n"
                    f"      {select_additions}\n"
                    f"    FROM {_quote_identifier(input_id)}\n"
                    "  )"
                )
                basis = "Derived fields translated to DuckDB SELECT expressions."
                locator = f"cte:{operation_id}"
            elif kind == "write":
                write_operation = operation
                basis = "Parquet write translated to a DuckDB COPY statement."
                locator = f"statement:copy:{operation_id}"
            else:  # Defensive: the capability pass must diagnose this.
                raise RuntimeError(f"Unaccounted DuckDB operation kind: {kind}")

            records_by_id[operation_id] = TranslationRecord(
                source_refs=source_refs,
                operation_id=operation_id,
                operation_pointer=f"/pipeline/operations/{operation_index}",
                target_refs=(TargetReference(artifact=TARGET_PATH, locator=locator),),
                disposition=Disposition.PRESERVED,
                basis=basis,
            )

        if write_operation is None:  # Defensive: checked during capability analysis.
            raise RuntimeError("DuckDB translation requires one write operation.")
        write_id = str(write_operation["id"])
        write_input = predecessors[write_id][0]
        target_dataset = datasets[str(write_operation["dataset_id"])]
        target_path = str(_object(target_dataset["location"])["uri"])
        cte_text = ",\n  ".join(ctes)
        sql = (
            "-- Generated by ETLIR from a validated Pipeline Semantic Model.\n"
            "COPY (\n"
            "  WITH\n"
            f"  {cte_text}\n"
            "  SELECT *\n"
            f"  FROM {_quote_identifier(write_input)}\n"
            ")\n"
            f"TO {_quote_literal(target_path)}\n"
            "(FORMAT PARQUET);\n"
        )
        artifact = GeneratedArtifact(
            role="target",
            path=TARGET_PATH,
            media_type="text/sql",
            content=sql.encode("utf-8"),
        )
        ordered_records = tuple(records_by_id[str(item["id"])] for item in operations)
        return TranslationResult(
            descriptor=self.descriptor,
            artifacts=(artifact,),
            records=ordered_records,
        )

    def _capability_diagnostics(
        self,
        model: JsonObject,
        operations: list[JsonObject],
        datasets: dict[str, JsonObject],
        edges: list[JsonObject],
    ) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        model_version = str(model["model_version"])
        if model_version not in self.descriptor.supported_model_versions:
            diagnostics.append(
                make_diagnostic(
                    code="TARGET.UNSUPPORTED_MODEL_VERSION",
                    severity="error",
                    category="unsupported",
                    stage="target_translation",
                    message=f"DuckDB adapter does not support model version '{model_version}'.",
                    artifact="pipeline-model.json",
                    pointer="/model_version",
                    remediation="Use a compatible adapter or explicitly upgrade the model.",
                )
            )

        if not _is_linear_shape(operations, edges):
            diagnostics.append(
                make_diagnostic(
                    code="TARGET.UNSUPPORTED_SHAPE",
                    severity="error",
                    category="unsupported",
                    stage="target_translation",
                    message=(
                        "DuckDB v0.1 supports one linear flow with one read and one "
                        "terminal write."
                    ),
                    artifact="pipeline-model.json",
                    pointer="/pipeline/edges",
                    remediation=(
                        "Use a target adapter that supports branching or simplify the "
                        "example flow."
                    ),
                )
            )

        for index, operation in enumerate(operations):
            operation_id = str(operation["id"])
            kind = str(operation["kind"])
            pointer = f"/pipeline/operations/{index}"
            if kind == "external_operation":
                diagnostics.append(
                    make_diagnostic(
                        code="TARGET.UNSUPPORTED_OPERATION",
                        severity="error",
                        category="unsupported",
                        stage="target_translation",
                        message=(
                            "DuckDB SQL cannot preserve external operation "
                            f"'{operation_id}' ({operation['operation_type']})."
                        ),
                        artifact="pipeline-model.json",
                        pointer=pointer,
                        operation_id=operation_id,
                        details={"operation_kind": kind, "target_adapter": self.descriptor.name},
                        remediation=(
                            "Implement the operation outside SQL or choose a target adapter "
                            "with this capability."
                        ),
                    )
                )
                continue
            if kind not in {"read", "filter", "derive", "write"}:
                diagnostics.append(
                    make_diagnostic(
                        code="TARGET.UNSUPPORTED_OPERATION",
                        severity="error",
                        category="unsupported",
                        stage="target_translation",
                        message=f"DuckDB adapter does not support operation kind '{kind}'.",
                        artifact="pipeline-model.json",
                        pointer=pointer,
                        operation_id=operation_id,
                        remediation=(
                            "Choose a compatible target adapter or implement this operation "
                            "capability."
                        ),
                    )
                )
                continue

            if kind in {"read", "write"}:
                dataset = datasets[str(operation["dataset_id"])]
                expected_format = "csv" if kind == "read" else "parquet"
                if dataset["kind"] != "file" or dataset["format"] != expected_format:
                    diagnostics.append(
                        make_diagnostic(
                            code="TARGET.UNSUPPORTED_DATASET",
                            severity="error",
                            category="unsupported",
                            stage="target_translation",
                            message=(
                                f"DuckDB {kind} requires a file dataset in {expected_format} "
                                "format; "
                                f"'{dataset['id']}' is {dataset['kind']}/{dataset['format']}."
                            ),
                            artifact="pipeline-model.json",
                            pointer=f"{pointer}/dataset_id",
                            operation_id=operation_id,
                            remediation="Use a supported file format or extend the DuckDB adapter.",
                        )
                    )

            expressions = _operation_expressions(operation)
            for expression in expressions:
                unsupported = _find_unsupported_expression(expression)
                if unsupported is not None:
                    diagnostics.append(
                        make_diagnostic(
                            code="TARGET.UNSUPPORTED_EXPRESSION",
                            severity="error",
                            category="unsupported",
                            stage="target_translation",
                            message=f"Operation '{operation_id}' uses {unsupported}.",
                            artifact="pipeline-model.json",
                            pointer=pointer,
                            operation_id=operation_id,
                            remediation=(
                                "Rewrite the expression or add an explicit DuckDB "
                                "translation rule."
                            ),
                        )
                    )

        return diagnostics

    @staticmethod
    def _blocked_records(
        operations: list[JsonObject],
        diagnostics: list[Diagnostic],
    ) -> tuple[TranslationRecord, ...]:
        by_operation: dict[str, list[Diagnostic]] = defaultdict(list)
        for diagnostic in diagnostics:
            operation_id = diagnostic.location.get("operation_id")
            if isinstance(operation_id, str):
                by_operation[operation_id].append(diagnostic)

        records: list[TranslationRecord] = []
        for index, operation in enumerate(operations):
            operation_id = str(operation["id"])
            operation_diagnostics = by_operation.get(operation_id, [])
            if operation_diagnostics:
                disposition = Disposition.UNSUPPORTED
                basis = "The target adapter reported an unsupported capability for this operation."
            else:
                disposition = Disposition.BLOCKED
                basis = (
                    "No target artifact was emitted because the pipeline contains a "
                    "blocking condition."
                )
            records.append(
                TranslationRecord(
                    source_refs=tuple(_objects(operation["source_refs"])),
                    operation_id=operation_id,
                    operation_pointer=f"/pipeline/operations/{index}",
                    target_refs=(),
                    disposition=disposition,
                    basis=basis,
                    diagnostic_ids=tuple(item.id for item in operation_diagnostics),
                )
            )
        return tuple(records)


def _is_linear_shape(operations: list[JsonObject], edges: list[JsonObject]) -> bool:
    if not operations:
        return False
    kinds = [str(operation["kind"]) for operation in operations]
    if kinds.count("read") != 1 or kinds.count("write") != 1:
        return False
    if len(edges) != len(operations) - 1:
        return False

    predecessors = _predecessors(edges)
    successors = _successors(edges)
    for operation in operations:
        operation_id = str(operation["id"])
        kind = str(operation["kind"])
        expected_in = 0 if kind == "read" else 1
        expected_out = 0 if kind == "write" else 1
        if len(predecessors[operation_id]) != expected_in:
            return False
        if len(successors[operation_id]) != expected_out:
            return False
    return len(_topological_order(operations, edges)) == len(operations)


def _topological_order(
    operations: list[JsonObject],
    edges: list[JsonObject],
) -> list[JsonObject]:
    by_id = {str(operation["id"]): operation for operation in operations}
    successors = _successors(edges)
    indegree = {identifier: 0 for identifier in by_id}
    for edge in edges:
        indegree[str(edge["to"])] += 1
    queue = deque(identifier for identifier in by_id if indegree[identifier] == 0)
    ordered: list[JsonObject] = []
    while queue:
        current = queue.popleft()
        ordered.append(by_id[current])
        for successor in successors[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    return ordered


def _predecessors(edges: list[JsonObject]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        result[str(edge["to"])].append(str(edge["from"]))
    return result


def _successors(edges: list[JsonObject]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        result[str(edge["from"])].append(str(edge["to"]))
    return result


def _operation_expressions(operation: JsonObject) -> list[JsonObject]:
    kind = operation["kind"]
    if kind == "filter":
        return [_object(operation["predicate"])]
    if kind == "derive":
        return [_object(item["expression"]) for item in _objects(operation["assignments"])]
    return []


def _find_unsupported_expression(expression: JsonObject) -> str | None:
    kind = str(expression["kind"])
    if kind == "parameter":
        return "a parameter expression, which DuckDB v0.1 does not translate"
    if kind in {"field", "literal"}:
        return None
    if kind == "binary":
        left = _find_unsupported_expression(_object(expression["left"]))
        if left:
            return left
        return _find_unsupported_expression(_object(expression["right"]))
    return f"unsupported expression kind '{kind}'"


def _render_expression(expression: JsonObject) -> str:
    kind = str(expression["kind"])
    if kind == "field":
        return _quote_identifier(str(expression["name"]))
    if kind == "literal":
        return _quote_literal(expression["value"])
    if kind == "binary":
        operators = {
            "eq": "=",
            "ne": "<>",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
            "add": "+",
            "subtract": "-",
            "multiply": "*",
            "divide": "/",
            "and": "AND",
            "or": "OR",
        }
        left = _render_expression(_object(expression["left"]))
        right = _render_expression(_object(expression["right"]))
        return f"({left} {operators[str(expression['operator'])]} {right})"
    raise RuntimeError(f"Expression kind was not cleared by capability analysis: {kind}")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _render_csv_columns(fields: list[JsonObject]) -> str:
    type_mapping = {
        "string": "VARCHAR",
        "integer": "BIGINT",
        "number": "DOUBLE",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "decimal": "DECIMAL(38, 10)",
        "unknown": "VARCHAR",
    }
    entries = [
        f"{_quote_literal(str(field['name']))}: "
        f"{_quote_literal(type_mapping[str(field['type'])])}"
        for field in fields
    ]
    return "{" + ", ".join(entries) + "}"


def _object(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object after successful model validation.")
    return value


def _objects(value: Any) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError("Expected a list of JSON objects after successful model validation.")
    return value
