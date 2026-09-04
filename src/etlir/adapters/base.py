"""Protocols implemented by source and target adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from etlir.types import AdaptationResult, AdapterDescriptor, JsonObject, TranslationResult


class SourceAdapter(Protocol):
    """Adapt a source-format artifact into a Pipeline Semantic Model."""

    descriptor: AdapterDescriptor

    def adapt(self, source_path: Path) -> AdaptationResult:
        """Read and account for one source artifact."""
        ...


class TargetAdapter(Protocol):
    """Translate a valid Pipeline Semantic Model into target artifacts."""

    descriptor: AdapterDescriptor

    def translate(self, model: JsonObject) -> TranslationResult:
        """Translate or explicitly diagnose every model operation."""
        ...

