"""Cross-boundary fidelity accounting."""

from __future__ import annotations

from collections import Counter
from typing import Any

from etlir.diagnostics import Diagnostic, make_diagnostic
from etlir.types import (
    AdaptationResult,
    AssessmentResult,
    Disposition,
    JsonObject,
    RunStatus,
    TranslationResult,
)


def assess_fidelity(
    adaptation: AdaptationResult,
    model: JsonObject,
    translation: TranslationResult,
    prior_diagnostics: tuple[Diagnostic, ...] = (),
) -> AssessmentResult:
    """Enforce source-to-model and model-to-target accounting invariants."""

    diagnostics: list[Diagnostic] = []
    pipeline = _object(model["pipeline"])
    operations = _objects(pipeline["operations"])

    modeled_source_refs = {
        (str(reference["artifact_id"]), str(reference["pointer"]))
        for operation in operations
        for reference in _objects(operation["source_refs"])
    }
    diagnosed_source_refs = {
        (adaptation.source_artifact.artifact_id, str(diagnostic.location["pointer"]))
        for diagnostic in adaptation.diagnostics
        if diagnostic.location.get("pointer") is not None
    }
    for element in adaptation.source_elements:
        key = (element.artifact_id, element.pointer)
        if key not in modeled_source_refs and key not in diagnosed_source_refs:
            diagnostics.append(
                make_diagnostic(
                    code="FIDELITY.UNACCOUNTED_SOURCE",
                    severity="error",
                    category="internal",
                    stage="fidelity_assessment",
                    message=f"Source operation '{element.element_id}' was not accounted for.",
                    artifact=adaptation.source_artifact.name,
                    pointer=element.pointer,
                    operation_id=element.element_id,
                    details={"source_kind": element.kind},
                    remediation="Update the Source Adapter to map or diagnose this operation.",
                )
            )

    record_counts = Counter(record.operation_id for record in translation.records)
    operation_ids = {str(operation["id"]) for operation in operations}
    target_artifact_paths = {artifact.path for artifact in translation.artifacts}
    for index, operation in enumerate(operations):
        operation_id = str(operation["id"])
        count = record_counts[operation_id]
        if count == 0:
            diagnostics.append(
                make_diagnostic(
                    code="FIDELITY.UNACCOUNTED_MODEL",
                    severity="error",
                    category="internal",
                    stage="fidelity_assessment",
                    message=f"Model operation '{operation_id}' has no target disposition.",
                    artifact="pipeline-model.json",
                    pointer=f"/pipeline/operations/{index}",
                    operation_id=operation_id,
                    remediation="Update the Target Adapter to return one translation record.",
                )
            )
        elif count > 1:
            diagnostics.append(
                make_diagnostic(
                    code="FIDELITY.DUPLICATE_MODEL_RECORD",
                    severity="error",
                    category="internal",
                    stage="fidelity_assessment",
                    message=f"Model operation '{operation_id}' has {count} target records.",
                    artifact="pipeline-model.json",
                    pointer=f"/pipeline/operations/{index}",
                    operation_id=operation_id,
                    remediation="Return exactly one translation record for each model operation.",
                )
            )

    for record in translation.records:
        if record.operation_id not in operation_ids:
            diagnostics.append(
                make_diagnostic(
                    code="FIDELITY.UNKNOWN_MODEL_RECORD",
                    severity="error",
                    category="internal",
                    stage="fidelity_assessment",
                    message=f"Target record references unknown operation '{record.operation_id}'.",
                    artifact="pipeline-model.json",
                    pointer=record.operation_pointer,
                    operation_id=record.operation_id,
                    remediation="Remove the record or correct its model operation reference.",
                )
            )
        if record.disposition == Disposition.PRESERVED and not record.target_refs:
            diagnostics.append(
                make_diagnostic(
                    code="FIDELITY.PRESERVED_WITHOUT_TARGET",
                    severity="error",
                    category="internal",
                    stage="fidelity_assessment",
                    message=(
                        f"Operation '{record.operation_id}' is marked preserved without "
                        "a target reference."
                    ),
                    artifact="pipeline-model.json",
                    pointer=record.operation_pointer,
                    operation_id=record.operation_id,
                    remediation="Add a target artifact locator or use a different disposition.",
                )
            )
        if record.disposition == Disposition.UNSUPPORTED and not record.diagnostic_ids:
            diagnostics.append(
                make_diagnostic(
                    code="FIDELITY.UNSUPPORTED_WITHOUT_DIAGNOSTIC",
                    severity="error",
                    category="internal",
                    stage="fidelity_assessment",
                    message=(
                        f"Operation '{record.operation_id}' is unsupported but has no "
                        "linked diagnostic."
                    ),
                    artifact="pipeline-model.json",
                    pointer=record.operation_pointer,
                    operation_id=record.operation_id,
                    remediation="Link a structured target diagnostic to the unsupported record.",
                )
            )
        for target_ref in record.target_refs:
            if target_ref.artifact not in target_artifact_paths:
                diagnostics.append(
                    make_diagnostic(
                        code="FIDELITY.UNKNOWN_TARGET_REFERENCE",
                        severity="error",
                        category="internal",
                        stage="fidelity_assessment",
                        message=(
                            f"Operation '{record.operation_id}' references target artifact "
                            f"'{target_ref.artifact}', which was not returned."
                        ),
                        artifact="pipeline-model.json",
                        pointer=record.operation_pointer,
                        operation_id=record.operation_id,
                        remediation="Return the target artifact or remove the invalid reference.",
                    )
                )

    available_diagnostic_ids = {
        diagnostic.id
        for diagnostic in (*prior_diagnostics, *translation.diagnostics, *diagnostics)
    }
    for record in translation.records:
        for diagnostic_id in record.diagnostic_ids:
            if diagnostic_id not in available_diagnostic_ids:
                diagnostics.append(
                    make_diagnostic(
                        code="FIDELITY.UNKNOWN_DIAGNOSTIC_REFERENCE",
                        severity="error",
                        category="internal",
                        stage="fidelity_assessment",
                        message=(
                            f"Operation '{record.operation_id}' references missing diagnostic "
                            f"'{diagnostic_id}'."
                        ),
                        artifact="pipeline-model.json",
                        pointer=record.operation_pointer,
                        operation_id=record.operation_id,
                        remediation="Return the referenced diagnostic with the translation result.",
                    )
                )

    dispositions = Counter(record.disposition.value for record in translation.records)
    summary: JsonObject = {
        "source_elements": len(adaptation.source_elements),
        "model_operations": len(operations),
        "preserved": dispositions[Disposition.PRESERVED.value],
        "approximated": dispositions[Disposition.APPROXIMATED.value],
        "unsupported": dispositions[Disposition.UNSUPPORTED.value],
        "ambiguous": dispositions[Disposition.AMBIGUOUS.value],
        "manual": dispositions[Disposition.MANUAL.value],
        "blocked": dispositions[Disposition.BLOCKED.value],
    }

    all_diagnostics = (*prior_diagnostics, *translation.diagnostics, *diagnostics)
    has_error = any(item.severity == "error" for item in all_diagnostics)
    has_warning = any(item.severity == "warning" for item in all_diagnostics)
    if has_error or dispositions["unsupported"] or dispositions["blocked"]:
        status = RunStatus.BLOCKED
    elif has_warning or any(
        dispositions[item] for item in ("approximated", "ambiguous", "manual")
    ):
        status = RunStatus.QUALIFIED
    else:
        status = RunStatus.COMPLETE

    return AssessmentResult(
        status=status,
        summary=summary,
        diagnostics=tuple(diagnostics),
    )


def _object(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object after successful model validation.")
    return value


def _objects(value: Any) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError("Expected a list of JSON objects after successful model validation.")
    return value
