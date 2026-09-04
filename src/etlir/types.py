"""Shared, implementation-level data contracts for adapters and the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeAlias

from etlir.diagnostics import Diagnostic

JsonObject: TypeAlias = dict[str, Any]


class AdapterKind(StrEnum):
    """Kinds of adapter recognized by the reference implementation."""

    SOURCE = "source"
    TARGET = "target"


class Disposition(StrEnum):
    """How a target adapter accounted for one model operation."""

    PRESERVED = "preserved"
    APPROXIMATED = "approximated"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"
    MANUAL = "manual"
    BLOCKED = "blocked"


class RunStatus(StrEnum):
    """Overall result of a translation run."""

    COMPLETE = "complete"
    QUALIFIED = "qualified"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AdapterDescriptor:
    """Public identity and compatibility declaration for an adapter."""

    name: str
    kind: AdapterKind
    version: str
    supported_model_versions: tuple[str, ...]


@dataclass(frozen=True)
class SourceArtifact:
    """Content identity for the input artifact."""

    artifact_id: str
    name: str
    format: str
    format_version: str
    sha256: str
    path: Path = field(repr=False)


@dataclass(frozen=True)
class SourceElement:
    """One source operation that must be accounted for during adaptation."""

    artifact_id: str
    pointer: str
    element_id: str
    kind: str


@dataclass(frozen=True)
class GeneratedArtifact:
    """A target artifact held in memory until the engine writes it safely."""

    role: str
    path: str
    media_type: str
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class TargetReference:
    """A stable logical location inside a generated artifact."""

    artifact: str
    locator: str

    def to_dict(self) -> JsonObject:
        return {"artifact": self.artifact, "locator": self.locator}


@dataclass(frozen=True)
class TranslationRecord:
    """Target-side accounting for one Pipeline Semantic Model operation."""

    source_refs: tuple[JsonObject, ...]
    operation_id: str
    operation_pointer: str
    target_refs: tuple[TargetReference, ...]
    disposition: Disposition
    basis: str
    diagnostic_ids: tuple[str, ...] = ()

    def to_dict(self) -> JsonObject:
        return {
            "source_refs": [dict(reference) for reference in self.source_refs],
            "model_ref": {
                "operation_id": self.operation_id,
                "pointer": self.operation_pointer,
            },
            "target_refs": [reference.to_dict() for reference in self.target_refs],
            "disposition": self.disposition.value,
            "basis": self.basis,
            "diagnostic_ids": list(self.diagnostic_ids),
        }


@dataclass(frozen=True)
class AdaptationResult:
    """Result returned by a Source Adapter."""

    descriptor: AdapterDescriptor
    source_artifact: SourceArtifact
    source_elements: tuple[SourceElement, ...]
    model: JsonObject | None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class TranslationResult:
    """Result returned by a Target Adapter."""

    descriptor: AdapterDescriptor
    artifacts: tuple[GeneratedArtifact, ...]
    records: tuple[TranslationRecord, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class AssessmentResult:
    """Output from cross-boundary fidelity accounting."""

    status: RunStatus
    summary: JsonObject
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class RunResult:
    """Public result from the end-to-end engine."""

    status: RunStatus
    exit_code: int
    output_directory: Path
    evidence: JsonObject

