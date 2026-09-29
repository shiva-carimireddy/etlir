"""Extension contracts: source adapters and target emitters.

The core depends on neither side. A source adapter produces a source-specific Raw IR and
normalizes it to Canonical IR. A target emitter consumes Canonical IR only and produces a
target package. Neither may import the other; tests enforce this boundary.

See docs/extending.md for the contributor guide and the conformance kit in
:mod:`etlir.testing`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from etlir.canonical.model import CanonicalDocument
from etlir.capabilities import CapabilityManifest
from etlir.evidence import Diagnostic, EvidenceRecord


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputArtifact(_Model):
    path: str = Field(description="Path relative to the input root, with '/' separators.")
    sha256: str
    size: int


class RawBundle(_Model):
    """Source-preserving Raw IR produced by a source adapter.

    ``payload`` is adapter-defined and must be JSON-serializable and deterministic.
    ``omissions`` lists source facts the adapter intentionally did not retain; an adapter
    may call its Raw IR lossless only if a defined preservation test proves it.
    """

    adapter: str
    adapter_version: str
    raw_ir_version: str
    inputs: list[InputArtifact]
    payload: dict[str, Any]
    omissions: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class NormalizationResult(_Model):
    document: CanonicalDocument
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)


class EmitResult(_Model):
    artifacts: list[str] = Field(description="Written paths relative to the output directory.")
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)


class SourceAdapter(ABC):
    """Reads one source format into Raw IR, then normalizes it into Canonical IR."""

    id: ClassVar[str]
    version: ClassVar[str]
    ir_version: ClassVar[str]
    description: ClassVar[str] = ""

    @abstractmethod
    def accepts(self, path: Path) -> bool:
        """Cheaply decide whether ``path`` looks like input for this adapter."""

    @abstractmethod
    def load(self, inputs: Sequence[Path], root: Path) -> RawBundle:
        """Parse all inputs of one corpus group together into Raw IR.

        Cross-file references must be resolved against the whole group, not per file.
        """

    @abstractmethod
    def normalize(self, raw: RawBundle) -> NormalizationResult:
        """Produce Canonical IR plus provenance evidence and diagnostics."""


class TargetEmitter(ABC):
    """Lowers Canonical IR to one target profile's plan and package."""

    id: ClassVar[str]
    version: ClassVar[str]
    ir_versions: ClassVar[str]  # PEP 440 specifier, e.g. ">=0.1,<0.2"
    description: ClassVar[str] = ""

    @abstractmethod
    def capabilities(self) -> CapabilityManifest:
        """Declare per-construct support. Undeclared constructs are blocked."""

    @abstractmethod
    def plan(self, document: CanonicalDocument) -> dict[str, Any]:
        """Build a deterministic, JSON-serializable target plan. Must not mutate input."""

    @abstractmethod
    def write(self, plan: dict[str, Any], out_dir: Path) -> EmitResult:
        """Write the target package for ``plan`` under ``out_dir``."""
