"""Source Adapter for the synthetic ExampleFlow v1 format."""

from __future__ import annotations

import copy
import hashlib
import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from etlir.diagnostics import Diagnostic, make_diagnostic
from etlir.schema import iter_validation_errors, json_pointer, load_json_schema
from etlir.types import (
    AdaptationResult,
    AdapterDescriptor,
    AdapterKind,
    JsonObject,
    SourceArtifact,
    SourceElement,
)


class ExampleFlowSourceAdapter:
    """Adapt a fictional ExampleFlow JSON document into model v0.1.0."""

    descriptor = AdapterDescriptor(
        name="exampleflow",
        kind=AdapterKind.SOURCE,
        version="1.0.0",
        supported_model_versions=("0.1.0",),
    )

    def adapt(self, source_path: Path) -> AdaptationResult:
        raw = source_path.read_bytes()
        source_artifact = SourceArtifact(
            artifact_id="source",
            name=source_path.name,
            format="exampleflow",
            format_version="1.0",
            sha256=hashlib.sha256(raw).hexdigest(),
            path=source_path,
        )

        try:
            parsed = json.loads(raw)
        except (JSONDecodeError, UnicodeDecodeError) as error:
            diagnostic = make_diagnostic(
                code="SOURCE.INVALID_JSON",
                severity="error",
                category="invalid",
                stage="source_adaptation",
                message=f"The ExampleFlow source is not valid UTF-8 JSON: {error}",
                artifact=source_path.name,
                remediation="Supply a valid UTF-8 ExampleFlow v1 JSON document.",
            )
            return AdaptationResult(
                descriptor=self.descriptor,
                source_artifact=source_artifact,
                source_elements=(),
                model=None,
                diagnostics=(diagnostic,),
            )

        schema_path = Path(__file__).with_name("exampleflow.schema.json")
        schema = load_json_schema(schema_path)
        schema_errors = iter_validation_errors(parsed, schema)
        if schema_errors:
            schema_diagnostics = tuple(
                make_diagnostic(
                    code="SOURCE.SCHEMA_INVALID",
                    severity="error",
                    category="invalid",
                    stage="source_adaptation",
                    message=error.message,
                    artifact=source_path.name,
                    pointer=json_pointer(list(error.absolute_path)),
                    remediation="Correct the ExampleFlow document before adaptation.",
                )
                for error in schema_errors
            )
            return AdaptationResult(
                descriptor=self.descriptor,
                source_artifact=source_artifact,
                source_elements=(),
                model=None,
                diagnostics=schema_diagnostics,
            )

        document = _object(parsed)
        pipeline = _object(document["pipeline"])
        steps = _objects(pipeline["steps"])
        source_elements = tuple(
            SourceElement(
                artifact_id="source",
                pointer=f"/pipeline/steps/{index}",
                element_id=str(step["id"]),
                kind=str(step["type"]),
            )
            for index, step in enumerate(steps)
        )

        diagnostics: list[Diagnostic] = []
        operations: list[JsonObject] = []
        edges: list[JsonObject] = []
        for index, step in enumerate(steps):
            operation = self._adapt_step(step, index, source_path.name, diagnostics)
            if operation is None:
                continue
            operations.append(operation)
            for input_id in _strings(step["inputs"]):
                edges.append({"from": input_id, "to": str(step["id"])})

        if diagnostics:
            return AdaptationResult(
                descriptor=self.descriptor,
                source_artifact=source_artifact,
                source_elements=source_elements,
                model=None,
                diagnostics=tuple(diagnostics),
            )

        datasets = [self._adapt_dataset(dataset) for dataset in _objects(pipeline["datasets"])]
        normalized_pipeline: JsonObject = {
            "id": pipeline["id"],
            "name": pipeline["name"],
            "parameters": [],
            "datasets": datasets,
            "operations": operations,
            "edges": edges,
        }
        if "description" in pipeline:
            normalized_pipeline["description"] = pipeline["description"]

        model: JsonObject = {
            "model_version": "0.1.0",
            "provenance": {
                "source_adapter": {
                    "name": self.descriptor.name,
                    "version": self.descriptor.version,
                },
                "source_artifact": {
                    "id": source_artifact.artifact_id,
                    "name": source_artifact.name,
                    "sha256": source_artifact.sha256,
                    "format": source_artifact.format,
                    "format_version": source_artifact.format_version,
                },
            },
            "pipeline": normalized_pipeline,
        }
        return AdaptationResult(
            descriptor=self.descriptor,
            source_artifact=source_artifact,
            source_elements=source_elements,
            model=model,
        )

    @staticmethod
    def _adapt_dataset(dataset: JsonObject) -> JsonObject:
        normalized: JsonObject = {
            "id": dataset["id"],
            "role": dataset["role"],
            "kind": dataset["kind"],
            "format": dataset["format"],
            "location": {"uri": dataset["path"]},
            "fields": copy.deepcopy(dataset["fields"]),
        }
        if "name" in dataset:
            normalized["name"] = dataset["name"]
        return normalized

    @staticmethod
    def _adapt_step(
        step: JsonObject,
        index: int,
        artifact_name: str,
        diagnostics: list[Diagnostic],
    ) -> JsonObject | None:
        step_type = str(step["type"])
        operation: JsonObject = {
            "id": step["id"],
            "kind": {
                "external_http": "external_operation",
            }.get(step_type, step_type),
            "source_refs": [
                {
                    "artifact_id": "source",
                    "pointer": f"/pipeline/steps/{index}",
                }
            ],
        }
        if "name" in step:
            operation["name"] = step["name"]

        if step_type in {"read", "write"}:
            operation["dataset_id"] = step["dataset"]
        elif step_type == "filter":
            operation["predicate"] = copy.deepcopy(step["condition"])
        elif step_type == "derive":
            operation["assignments"] = copy.deepcopy(step["assignments"])
        elif step_type == "external_http":
            operation["operation_type"] = "http_request"
            operation["configuration"] = {
                "method": step["method"],
                "url": step["url"],
                "timeout_seconds": step["timeout_seconds"],
            }
        else:  # Defensive: the source schema should reject this first.
            diagnostics.append(
                make_diagnostic(
                    code="SOURCE.UNACCOUNTED_OPERATION",
                    severity="error",
                    category="internal",
                    stage="source_adaptation",
                    message=f"ExampleFlow step type '{step_type}' has no adaptation rule.",
                    artifact=artifact_name,
                    pointer=f"/pipeline/steps/{index}",
                    operation_id=str(step["id"]),
                    remediation="Implement an explicit mapping or diagnostic for this step type.",
                )
            )
            return None
        return operation


def _object(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object after successful source schema validation.")
    return value


def _objects(value: Any) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError(
            "Expected a list of JSON objects after successful source schema validation."
        )
    return value


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("Expected a list of strings after successful source schema validation.")
    return value
