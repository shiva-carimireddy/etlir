"""Adapter interfaces and discovery."""

from etlir.adapters.base import SourceAdapter, TargetAdapter
from etlir.adapters.registry import AdapterRegistry

__all__ = ["AdapterRegistry", "SourceAdapter", "TargetAdapter"]

