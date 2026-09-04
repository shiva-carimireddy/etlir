"""End-to-end ETLIR translation orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path, PurePosixPath

from etlir.adapters.registry import AdapterRegistry
from etlir.diagnostics import Diagnostic
from etlir.evidence import build_evidence, canonical_json_bytes, render_evidence_markdown
from etlir.fidelity import assess_fidelity
from etlir.types import (
    AssessmentResult,
    GeneratedArtifact,
    JsonObject,
    RunResult,
    RunStatus,
    TranslationResult,
)
from etlir.validation import validate_model


class ETLIREngineError(RuntimeError):
    """A safe, user-facing engine failure."""


def translate(
    *,
    source_path: Path,
    output_directory: Path,
    source_adapter_name: str,
    target_adapter_name: str,
    registry: AdapterRegistry | None = None,
    created_at: datetime | None = None,
) -> RunResult:
    """Run adaptation, validation, translation, assessment, and evidence generation."""

    if not source_path.is_file():
        raise ETLIREngineError(f"Source artifact does not exist or is not a file: {source_path}")
    _require_empty_output(output_directory)

    adapter_registry = registry or AdapterRegistry()
    source_adapter = adapter_registry.source(source_adapter_name)
    target_adapter = adapter_registry.target(target_adapter_name)
    adaptation = source_adapter.adapt(source_path)

    model = adaptation.model
    model_bytes: bytes | None = None
    model_diagnostics: tuple[Diagnostic, ...] = ()
    if model is not None:
        model_bytes = canonical_json_bytes(model)
        model_diagnostics = validate_model(model)

    model_valid = model is not None and not _has_errors(
        (*adaptation.diagnostics, *model_diagnostics)
    )
    if model_valid and model is not None:
        translation = target_adapter.translate(model)
        prior_diagnostics = (*adaptation.diagnostics, *model_diagnostics)
        assessment = assess_fidelity(
            adaptation,
            model,
            translation,
            prior_diagnostics=prior_diagnostics,
        )
    else:
        translation = TranslationResult(
            descriptor=target_adapter.descriptor,
            artifacts=(),
            records=(),
        )
        operation_count = 0
        if model is not None:
            operation_count = len(_objects(_object(model["pipeline"])["operations"]))
        assessment = AssessmentResult(
            status=RunStatus.BLOCKED,
            summary={
                "source_elements": len(adaptation.source_elements),
                "model_operations": operation_count,
                "preserved": 0,
                "approximated": 0,
                "unsupported": 0,
                "ambiguous": 0,
                "manual": 0,
                "blocked": operation_count,
            },
            diagnostics=(),
        )

    diagnostics = (
        *adaptation.diagnostics,
        *model_diagnostics,
        *translation.diagnostics,
        *assessment.diagnostics,
    )
    model_version = str(model["model_version"]) if model is not None else None
    evidence = build_evidence(
        adaptation=adaptation,
        translation=translation,
        assessment=assessment,
        model_bytes=model_bytes,
        diagnostics=diagnostics,
        model_validation_valid=model_valid,
        model_version=model_version,
        created_at=created_at,
    )

    output_directory.mkdir(parents=True, exist_ok=True)
    if model_bytes is not None:
        _write_file(output_directory, "pipeline-model.json", model_bytes)
    for artifact in translation.artifacts:
        _write_generated_artifact(output_directory, artifact)
    _write_file(output_directory, "evidence.json", canonical_json_bytes(evidence))
    _write_file(
        output_directory,
        "evidence.md",
        render_evidence_markdown(evidence).encode("utf-8"),
    )

    return RunResult(
        status=assessment.status,
        exit_code=2 if assessment.status == RunStatus.BLOCKED else 0,
        output_directory=output_directory,
        evidence=evidence,
    )


def _require_empty_output(output_directory: Path) -> None:
    if output_directory.exists() and not output_directory.is_dir():
        raise ETLIREngineError(f"Output path is not a directory: {output_directory}")
    if output_directory.exists() and any(output_directory.iterdir()):
        raise ETLIREngineError(
            f"Output directory is not empty: {output_directory}. Choose a new directory."
        )


def _write_generated_artifact(output_directory: Path, artifact: GeneratedArtifact) -> None:
    path = PurePosixPath(artifact.path)
    if path.is_absolute() or ".." in path.parts:
        raise ETLIREngineError(f"Adapter returned an unsafe artifact path: {artifact.path}")
    _write_file(output_directory, artifact.path, artifact.content)


def _write_file(output_directory: Path, relative_path: str, content: bytes) -> None:
    destination = output_directory.joinpath(*PurePosixPath(relative_path).parts)
    resolved_root = output_directory.resolve()
    resolved_destination = destination.resolve()
    if not resolved_destination.is_relative_to(resolved_root):
        raise ETLIREngineError(f"Refusing to write outside output directory: {relative_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def _has_errors(diagnostics: tuple[Diagnostic, ...]) -> bool:
    return any(diagnostic.severity == "error" for diagnostic in diagnostics)


def _object(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("Expected model object.")
    return value


def _objects(value: object) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError("Expected model object list.")
    return value

