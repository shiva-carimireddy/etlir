"""Diagnostics and the conversion evidence model.

A diagnostic reports a finding. An evidence record states what happened to one subject
(canonical entity or source construct) and where it ended up. Neither is proof that
source and target behave identically.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from etlir.canonical.model import SourceRef


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class EvidenceStatus(StrEnum):
    PRESERVED = "PRESERVED"
    TRANSFORMED_EQUIVALENT = "TRANSFORMED_EQUIVALENT"
    APPROXIMATED = "APPROXIMATED"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    BLOCKED = "BLOCKED"


class Diagnostic(_Model):
    code: str = Field(pattern=r"^[A-Z]+-[A-Z0-9]+-\d{3}$", description="Stable code, e.g. IR-V-001")
    severity: Severity
    message: str
    subject_id: str | None = None
    source: SourceRef | None = None


class TargetLocation(_Model):
    artifact: str = Field(description="Path relative to the output package root.")
    locator: str | None = Field(default=None, description="Symbol or line range in the artifact.")


class EvidenceRecord(_Model):
    subject_id: str
    status: EvidenceStatus
    rule: str | None = None
    source: SourceRef | None = None
    targets: list[TargetLocation] = Field(default_factory=list)
    diagnostic_codes: list[str] = Field(default_factory=list)


def has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(d.severity is Severity.ERROR for d in diagnostics)
