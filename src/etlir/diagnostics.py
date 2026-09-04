"""Structured diagnostic construction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, TypeAlias

JsonObject: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class Diagnostic:
    """A stable, serializable issue raised during the ETLIR lifecycle."""

    id: str
    code: str
    severity: str
    category: str
    stage: str
    message: str
    location: JsonObject
    details: JsonObject = field(default_factory=dict)
    remediation: str | None = None

    def to_dict(self) -> JsonObject:
        result: JsonObject = {
            "id": self.id,
            "code": self.code,
            "severity": self.severity,
            "category": self.category,
            "stage": self.stage,
            "message": self.message,
            "location": dict(self.location),
        }
        if self.details:
            result["details"] = dict(self.details)
        if self.remediation:
            result["remediation"] = self.remediation
        return result


def make_diagnostic(
    *,
    code: str,
    severity: str,
    category: str,
    stage: str,
    message: str,
    artifact: str,
    pointer: str | None = None,
    operation_id: str | None = None,
    details: JsonObject | None = None,
    remediation: str | None = None,
) -> Diagnostic:
    """Create a diagnostic with a deterministic content-derived identifier."""

    location: JsonObject = {"artifact": artifact}
    if pointer is not None:
        location["pointer"] = pointer
    if operation_id is not None:
        location["operation_id"] = operation_id

    identity = {
        "code": code,
        "stage": stage,
        "message": message,
        "location": location,
        "details": details or {},
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return Diagnostic(
        id=f"diag_{digest}",
        code=code,
        severity=severity,
        category=category,
        stage=stage,
        message=message,
        location=location,
        details=details or {},
        remediation=remediation,
    )

