from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from etlir.canonical.invariants import validate
from etlir.canonical.model import CanonicalDocument
from etlir.capabilities import CapabilityState, analyze
from etlir.contracts import SourceAdapter, TargetEmitter
from etlir.registry import RegistrationError, check_source, check_target
from etlir.serialization import dumps


@dataclass
class ConformanceReport:
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)


def _sources_of(doc: CanonicalDocument) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in doc.pipelines:
        out.append((p.id, p.source.adapter))
        out += [(t.id, t.source.adapter) for t in p.tasks]
        out += [(e.id, e.source.adapter) for e in p.expressions]
    for df in doc.dataflows:
        out.append((df.id, df.source.adapter))
        out += [(o.id, o.source.adapter) for o in df.operations]
        out += [(e.id, e.source.adapter) for e in df.expressions]
    for group in (doc.datasets, doc.parameters, doc.bindings):
        out += [(x.id, x.source.adapter) for x in group]
    return out


def check_source_adapter(
    adapter: SourceAdapter, inputs: Sequence[Path], root: Path
) -> ConformanceReport:
    report = ConformanceReport()
    try:
        check_source(type(adapter))
    except RegistrationError as exc:
        report.failures.append(f"registration: {exc}")
        return report

    raw1, raw2 = adapter.load(inputs, root), adapter.load(inputs, root)
    report.require(dumps(raw1) == dumps(raw2), "Raw IR is not deterministic across runs")
    report.require(raw1.adapter == adapter.id, "RawBundle.adapter does not match adapter id")

    res1, res2 = adapter.normalize(raw1), adapter.normalize(raw1)
    report.require(dumps(res1) == dumps(res2), "normalization is not deterministic across runs")
    report.require(dumps(raw1) == dumps(raw2), "normalize() mutated the Raw IR")

    doc = res1.document
    roundtrip = CanonicalDocument.model_validate_json(dumps(doc))
    report.require(roundtrip == doc, "Canonical IR does not survive a JSON round trip")
    structural = [d for d in validate(doc) if d.severity.value == "error"]
    report.require(not structural, f"invariant errors: {[d.code for d in structural]}")
    for subject, producer in _sources_of(doc):
        report.require(producer == adapter.id, f"'{subject}' has provenance from '{producer}'")
    return report


def check_target_emitter(
    emitter: TargetEmitter, document: CanonicalDocument, out_dir: Path
) -> ConformanceReport:
    report = ConformanceReport()
    try:
        check_target(type(emitter))
    except RegistrationError as exc:
        report.failures.append(f"registration: {exc}")
        return report

    manifest = emitter.capabilities()
    report.require(manifest.target == emitter.id, "manifest.target does not match emitter id")
    constructs = [r.construct_id for r in manifest.rules]
    report.require(len(constructs) == len(set(constructs)), "manifest has duplicate constructs")

    before = dumps(document)
    plan1, plan2 = emitter.plan(document), emitter.plan(document)
    report.require(dumps(document) == before, "plan() mutated the Canonical IR")
    report.require(dumps(plan1) == dumps(plan2), "target plan is not deterministic")

    cap = analyze(document, manifest)
    blocked = {d.subject_id for d in cap.decisions if d.state is CapabilityState.BLOCKED}
    result = emitter.write(plan1, out_dir / "a")
    emitter.write(plan1, out_dir / "b")
    for rel in result.artifacts:
        a, b = out_dir / "a" / rel, out_dir / "b" / rel
        report.require(a.is_file(), f"declared artifact '{rel}' was not written")
        if a.is_file() and b.is_file():
            report.require(a.read_bytes() == b.read_bytes(), f"'{rel}' is not deterministic")
    if blocked:
        reported = {d.subject_id for d in result.diagnostics} | {
            d.subject_id for d in cap.diagnostics
        }
        report.require(blocked <= reported, "blocked constructs were not reported")
    return report
