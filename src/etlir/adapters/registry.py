"""Built-in and third-party adapter discovery."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import cast

from etlir.adapters.base import SourceAdapter, TargetAdapter


class UnknownAdapterError(LookupError):
    """Raised when a requested adapter is not installed."""


class AdapterRegistry:
    """Resolve built-in adapters first, then Python package entry points."""

    def source(self, name: str) -> SourceAdapter:
        if name == "exampleflow":
            from etlir.adapters.sources.exampleflow import ExampleFlowSourceAdapter

            return ExampleFlowSourceAdapter()
        return self._load_source_entry_point(name)

    def target(self, name: str) -> TargetAdapter:
        if name == "duckdb":
            from etlir.adapters.targets.duckdb import DuckDBTargetAdapter

            return DuckDBTargetAdapter()
        return self._load_target_entry_point(name)

    def source_names(self) -> tuple[str, ...]:
        names = {"exampleflow"}
        names.update(point.name for point in entry_points(group="etlir.source_adapters"))
        return tuple(sorted(names))

    def target_names(self) -> tuple[str, ...]:
        names = {"duckdb"}
        names.update(point.name for point in entry_points(group="etlir.target_adapters"))
        return tuple(sorted(names))

    @staticmethod
    def _load_source_entry_point(name: str) -> SourceAdapter:
        matches = [
            point for point in entry_points(group="etlir.source_adapters") if point.name == name
        ]
        if not matches:
            raise UnknownAdapterError(f"Unknown source adapter: {name}")
        adapter_class = matches[0].load()
        return cast(SourceAdapter, adapter_class())

    @staticmethod
    def _load_target_entry_point(name: str) -> TargetAdapter:
        matches = [
            point for point in entry_points(group="etlir.target_adapters") if point.name == name
        ]
        if not matches:
            raise UnknownAdapterError(f"Unknown target adapter: {name}")
        adapter_class = matches[0].load()
        return cast(TargetAdapter, adapter_class())

