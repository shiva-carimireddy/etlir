"""Plugin discovery for source adapters and target emitters.

Adapters and emitters register through Python entry points, so a third party can ship
``etlir-source-<name>`` or ``etlir-target-<name>`` as an independent package::

    [project.entry-points."etlir.sources"]
    my-format = "my_package.adapter:MyAdapter"

Registration is rejected if the plugin does not implement the contract or does not
support the running Canonical IR version.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TypeVar

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from etlir.canonical.model import IR_VERSION
from etlir.contracts import SourceAdapter, TargetEmitter

SOURCE_GROUP = "etlir.sources"
TARGET_GROUP = "etlir.targets"

_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


class RegistrationError(ValueError):
    pass


T = TypeVar("T", SourceAdapter, TargetEmitter)


def _check_id(cls: type) -> str:
    ident = getattr(cls, "id", None)
    if not isinstance(ident, str) or not ident or not set(ident) <= _ID_CHARS:
        raise RegistrationError(f"{cls.__name__}.id must be lowercase kebab-case, got {ident!r}")
    if not isinstance(getattr(cls, "version", None), str):
        raise RegistrationError(f"{cls.__name__}.version must be a string")
    return ident


def check_source(cls: type[SourceAdapter], ir_version: str = IR_VERSION) -> None:
    if not (isinstance(cls, type) and issubclass(cls, SourceAdapter)):
        raise RegistrationError(f"{cls!r} does not subclass SourceAdapter")
    _check_id(cls)
    if Version(cls.ir_version).release[:2] != Version(ir_version).release[:2]:
        raise RegistrationError(
            f"source '{cls.id}' targets IR {cls.ir_version}, running IR is {ir_version}"
        )


def check_target(cls: type[TargetEmitter], ir_version: str = IR_VERSION) -> None:
    if not (isinstance(cls, type) and issubclass(cls, TargetEmitter)):
        raise RegistrationError(f"{cls!r} does not subclass TargetEmitter")
    _check_id(cls)
    try:
        spec = SpecifierSet(cls.ir_versions)
    except (InvalidSpecifier, AttributeError) as exc:
        raise RegistrationError(f"target '{cls.id}' has invalid ir_versions") from exc
    if not spec.contains(ir_version, prereleases=True):
        raise RegistrationError(
            f"target '{cls.id}' supports IR {cls.ir_versions}, running IR is {ir_version}"
        )


class Registry:
    def __init__(self) -> None:
        self.sources: dict[str, type[SourceAdapter]] = {}
        self.targets: dict[str, type[TargetEmitter]] = {}
        self.errors: dict[str, str] = {}

    def add_source(self, cls: type[SourceAdapter]) -> None:
        check_source(cls)
        if cls.id in self.sources and self.sources[cls.id] is not cls:
            raise RegistrationError(f"duplicate source id '{cls.id}'")
        self.sources[cls.id] = cls

    def add_target(self, cls: type[TargetEmitter]) -> None:
        check_target(cls)
        if cls.id in self.targets and self.targets[cls.id] is not cls:
            raise RegistrationError(f"duplicate target id '{cls.id}'")
        self.targets[cls.id] = cls

    @classmethod
    def discover(cls) -> Registry:
        """Load all installed plugins. Broken plugins are recorded, not fatal."""
        reg = cls()
        for group, add in ((SOURCE_GROUP, reg.add_source), (TARGET_GROUP, reg.add_target)):
            for ep in sorted(entry_points(group=group), key=lambda e: e.name):
                try:
                    add(ep.load())
                except Exception as exc:  # a broken plugin must not break the CLI
                    reg.errors[f"{group}:{ep.name}"] = f"{type(exc).__name__}: {exc}"
        return reg
