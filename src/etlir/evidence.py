"""Build and render Migration Evidence Bundles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime

from etlir import __version__
from etlir.diagnostics import Diagnostic
from etlir.schema import iter_validation_errors, load_spec_schema
from etlir.types import (
    AdaptationResult,
    AssessmentResult,
    GeneratedArtifact,
    JsonObject,
    TranslationResult,
)


def canonical_json_bytes(value: JsonObject) -> bytes:
    """Serialize public JSON artifacts deterministically."""

    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


def build_evidence(
    *,
    adaptation: AdaptationResult,
    translation: TranslationResult,
    assessment: AssessmentResult,
    model_bytes: bytes | None,
    diagnostics: tuple[Diagnostic, ...],
    model_validation_valid: bool,
    model_version: str | None,
    created_at: datetime | None = None,
) -> JsonObject:
    """Build and validate the machine-readable evidence document."""

    timestamp = (created_at or utc_now()).astimezone(UTC)
    artifacts: list[JsonObject] = [
        {
            "role": "source",
            "path": adaptation.source_artifact.name,
            "media_type": "application/json",
            "sha256": adaptation.source_artifact.sha256,
        }
    ]
    if model_bytes is not None:
        artifacts.append(
            {
                "role": "model",
                "path": "pipeline-model.json",
                "media_type": "application/json",
                "sha256": sha256_bytes(model_bytes),
            }
        )
    artifacts.extend(_artifact_metadata(translation.artifacts))

    run_identity = "|".join(
        [
            adaptation.source_artifact.sha256,
            adaptation.descriptor.name,
            adaptation.descriptor.version,
            translation.descriptor.name,
            translation.descriptor.version,
            model_version or "none",
        ]
    )
    run_digest = hashlib.sha256(run_identity.encode("utf-8")).hexdigest()[:16]
    evidence: JsonObject = {
        "evidence_version": "0.1.0",
        "etlir_version": __version__,
        "run_id": f"run_{run_digest}",
        "created_at": timestamp.isoformat().replace("+00:00", "Z"),
        "status": assessment.status.value,
        "source": {
            "adapter": adaptation.descriptor.name,
            "adapter_version": adaptation.descriptor.version,
        },
        "target": {
            "adapter": translation.descriptor.name,
            "adapter_version": translation.descriptor.version,
        },
        "model_validation": {
            "valid": model_validation_valid,
            "model_version": model_version,
        },
        "artifacts": artifacts,
        "summary": dict(assessment.summary),
        "records": [record.to_dict() for record in translation.records],
        "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics],
    }
    errors = iter_validation_errors(
        evidence,
        load_spec_schema("evidence-bundle.schema.json"),
        include_spec_registry=True,
    )
    if errors:
        messages = "; ".join(error.message for error in errors)
        raise ValueError(f"Generated evidence violates its schema: {messages}")
    return evidence


def render_evidence_markdown(evidence: JsonObject) -> str:
    """Render a deterministic human-readable view of JSON evidence."""

    summary = _object(evidence["summary"])
    source = _object(evidence["source"])
    target = _object(evidence["target"])
    lines = [
        "# ETLIR Migration Evidence",
        "",
        f"**Status:** `{evidence['status']}`  ",
        f"**Run ID:** `{evidence['run_id']}`  ",
        f"**Created:** `{evidence['created_at']}`  ",
        f"**Model version:** `{_object(evidence['model_validation'])['model_version']}`",
        "",
        "## Translation",
        "",
        f"- Source Adapter: `{source['adapter']} {source['adapter_version']}`",
        f"- Target Adapter: `{target['adapter']} {target['adapter_version']}`",
        "",
        "## Summary",
        "",
        "| Measure | Count |",
        "|---|---:|",
    ]
    for key in (
        "source_elements",
        "model_operations",
        "preserved",
        "approximated",
        "unsupported",
        "ambiguous",
        "manual",
        "blocked",
    ):
        lines.append(f"| {key.replace('_', ' ').title()} | {summary[key]} |")

    lines.extend(
        [
            "",
            "## Operation records",
            "",
            "| Model operation | Disposition | Target location | Basis |",
            "|---|---|---|---|",
        ]
    )
    records = _objects(evidence["records"])
    if records:
        for record in records:
            model_ref = _object(record["model_ref"])
            targets = ", ".join(
                f"`{item['artifact']}#{item['locator']}`"
                for item in _objects(record["target_refs"])
            ) or "—"
            lines.append(
                "| "
                f"`{_markdown(str(model_ref['operation_id']))}` | "
                f"`{record['disposition']}` | {targets} | "
                f"{_markdown(str(record['basis']))} |"
            )
    else:
        lines.append("| — | — | — | No target records were produced. |")

    lines.extend(["", "## Diagnostics", ""])
    diagnostic_values = _objects(evidence["diagnostics"])
    if not diagnostic_values:
        lines.append("No diagnostics.")
    else:
        for diagnostic in diagnostic_values:
            location = _object(diagnostic["location"])
            location_text = str(location["artifact"])
            if "pointer" in location:
                location_text += f"{location['pointer']}"
            lines.extend(
                [
                    f"### `{diagnostic['code']}`",
                    "",
                    f"- Severity: `{diagnostic['severity']}`",
                    f"- Category: `{diagnostic['category']}`",
                    f"- Stage: `{diagnostic['stage']}`",
                    f"- Location: `{location_text}`",
                    f"- Message: {_markdown(str(diagnostic['message']))}",
                ]
            )
            if "remediation" in diagnostic:
                lines.append(f"- Remediation: {_markdown(str(diagnostic['remediation']))}")
            lines.append("")

    lines.extend(
        [
            "## Interpretation boundary",
            "",
            "This evidence accounts for declared model operations and adapter translation rules. "
            "It is not proof of runtime or business equivalence. Data reconciliation, target "
            "testing, security review, and owner acceptance remain necessary.",
            "",
        ]
    )
    return "\n".join(lines)


def _artifact_metadata(artifacts: Iterable[GeneratedArtifact]) -> list[JsonObject]:
    return [
        {
            "role": artifact.role,
            "path": artifact.path,
            "media_type": artifact.media_type,
            "sha256": sha256_bytes(artifact.content),
        }
        for artifact in artifacts
    ]


def _markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _object(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("Expected evidence object.")
    return value


def _objects(value: object) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError("Expected evidence object list.")
    return value
