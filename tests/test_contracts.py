"""Contract tests: registration rules and the conformance kit, using interface test doubles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from etlir.canonical.model import CanonicalDocument
from etlir.registry import RegistrationError, Registry
from etlir.serialization import dumps
from etlir.testing import check_source_adapter, check_target_emitter
from tests.fixtures.plugins import PlanOnlyEmitter, ToyAdapter


def test_toy_adapter_conforms(toy_inputs: tuple[list[Path], Path]) -> None:
    report = check_source_adapter(ToyAdapter(), *toy_inputs)
    assert report.ok, report.failures


def test_plan_only_emitter_conforms(toy_doc: CanonicalDocument, tmp_path: Path) -> None:
    report = check_target_emitter(PlanOnlyEmitter(), toy_doc, tmp_path)
    assert report.ok, report.failures


def test_canonical_output_independent_of_emitter_choice(
    toy_inputs: tuple[list[Path], Path], tmp_path: Path
) -> None:
    adapter = ToyAdapter()
    doc = adapter.normalize(adapter.load(*toy_inputs)).document
    before = dumps(doc)
    for emitter in (PlanOnlyEmitter(), PlanOnlyEmitter(rules=[])):
        emitter.write(emitter.plan(doc), tmp_path / str(id(emitter)))
    assert dumps(doc) == before


def test_blocked_constructs_are_reported(toy_doc: CanonicalDocument, tmp_path: Path) -> None:
    emitter = PlanOnlyEmitter(rules=[])
    plan = emitter.plan(toy_doc)
    assert plan["blocked_tasks"] == ["t.orders"]
    assert check_target_emitter(emitter, toy_doc, tmp_path).ok


def test_nondeterministic_emitter_fails_kit(toy_doc: CanonicalDocument, tmp_path: Path) -> None:
    class Flaky(PlanOnlyEmitter):
        calls = 0

        def plan(self, document: CanonicalDocument) -> dict[str, Any]:
            Flaky.calls += 1
            return {**super().plan(document), "n": Flaky.calls}

    report = check_target_emitter(Flaky(), toy_doc, tmp_path)
    assert "target plan is not deterministic" in report.failures


def test_registry_rejects_incompatible_ir() -> None:
    class Future(PlanOnlyEmitter):
        ir_versions = ">=1.0"

    class OldSource(ToyAdapter):
        ir_version = "0.0.9"

    reg = Registry()
    with pytest.raises(RegistrationError, match="supports IR"):
        reg.add_target(Future)
    with pytest.raises(RegistrationError, match="targets IR"):
        reg.add_source(OldSource)


def test_registry_rejects_bad_ids_and_duplicates() -> None:
    class BadId(ToyAdapter):
        id = "Toy_JSON"

    class Twin(ToyAdapter):
        pass

    reg = Registry()
    with pytest.raises(RegistrationError, match="kebab-case"):
        reg.add_source(BadId)
    reg.add_source(ToyAdapter)
    with pytest.raises(RegistrationError, match="duplicate"):
        reg.add_source(Twin)
    with pytest.raises(RegistrationError, match="subclass"):
        reg.add_target(ToyAdapter)  # type: ignore[arg-type]
