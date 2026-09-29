"""Target capability manifests and generic capability analysis.

A target emitter declares, per canonical construct, whether it is supported, constrained
(supported only when stated preconditions hold), approximated, or blocked. Constructs the
manifest does not mention are blocked: analysis fails closed.

Blocking propagates along the task graph: a task is blocked if its own kind or any
construct in its dataflow is blocked, or if any upstream task it depends on is blocked.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from etlir.canonical.invariants import walk_expression
from etlir.canonical.model import CallNode, CanonicalDocument, OpaqueNode, SourceRef
from etlir.evidence import Diagnostic, Severity


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CapabilityState(str, Enum):
    SUPPORTED = "supported"
    CONSTRAINED = "constrained"
    APPROXIMATED = "approximated"
    BLOCKED = "blocked"


class CapabilityRule(_Model):
    construct_id: str = Field(
        description="Construct key: 'operation.<kind>', 'task.<kind>', "
        "'dependency.<condition>', 'function.<name>' or 'expression.opaque'."
    )
    state: CapabilityState
    preconditions: list[str] = Field(
        default_factory=list, description="Machine-checked by the emitter's preflight."
    )
    notes: str | None = None


class CapabilityManifest(_Model):
    target: str = Field(description="Registered target emitter id.")
    target_version: str
    ir_versions: str = Field(description="PEP 440 specifier of compatible IR versions.")
    rules: list[CapabilityRule]

    def lookup(self, construct: str) -> CapabilityRule | None:
        for rule in self.rules:
            if rule.construct_id == construct:
                return rule
        return None


class Decision(_Model):
    subject_id: str
    construct_id: str
    state: CapabilityState
    source: SourceRef


class CapabilityReport(_Model):
    target: str
    decisions: list[Decision]
    blocked_task_ids: list[str]
    diagnostics: list[Diagnostic]


def _state(manifest: CapabilityManifest, construct: str) -> CapabilityState:
    rule = manifest.lookup(construct)
    return rule.state if rule is not None else CapabilityState.BLOCKED


def analyze(doc: CanonicalDocument, manifest: CapabilityManifest) -> CapabilityReport:
    decisions: list[Decision] = []

    def decide(subject: str, construct: str, source: SourceRef) -> CapabilityState:
        state = _state(manifest, construct)
        decisions.append(
            Decision(subject_id=subject, construct_id=construct, state=state, source=source)
        )
        return state

    blocked_dataflows: set[str] = set()
    for df in doc.dataflows:
        for op in df.operations:
            if decide(op.id, f"operation.{op.spec.kind}", op.source) is CapabilityState.BLOCKED:
                blocked_dataflows.add(df.id)
        for expr in df.expressions:
            for node in walk_expression(expr.ast):
                construct = (
                    f"function.{node.function}"
                    if isinstance(node, CallNode)
                    else "expression.opaque"
                    if isinstance(node, OpaqueNode)
                    else None
                )
                if construct and decide(expr.id, construct, expr.source) is CapabilityState.BLOCKED:
                    blocked_dataflows.add(df.id)

    blocked_tasks: list[str] = []
    for pipeline in doc.pipelines:
        blocked: set[str] = set()
        for task in pipeline.tasks:
            if decide(task.id, f"task.{task.kind.value}", task.source) is CapabilityState.BLOCKED:
                blocked.add(task.id)
            if task.dataflow_id in blocked_dataflows:
                blocked.add(task.id)
            for dep in task.depends_on:
                construct = f"dependency.{dep.condition.value}"
                if decide(task.id, construct, task.source) is CapabilityState.BLOCKED:
                    blocked.add(task.id)
        # Propagate to every downstream task until a fixed point is reached.
        changed = True
        while changed:
            changed = False
            for task in pipeline.tasks:
                if task.id not in blocked and any(d.task_id in blocked for d in task.depends_on):
                    blocked.add(task.id)
                    changed = True
        blocked_tasks += [t.id for t in pipeline.tasks if t.id in blocked]

    diagnostics = [
        Diagnostic(
            code="CAP-B-001",
            severity=Severity.ERROR,
            message=f"'{d.construct_id}' is blocked for target '{manifest.target}'.",
            subject_id=d.subject_id,
            source=d.source,
        )
        for d in decisions
        if d.state is CapabilityState.BLOCKED
    ] + [
        Diagnostic(
            code="CAP-A-001",
            severity=Severity.WARNING,
            message=f"'{d.construct_id}' is approximated for target '{manifest.target}'.",
            subject_id=d.subject_id,
            source=d.source,
        )
        for d in decisions
        if d.state is CapabilityState.APPROXIMATED
    ]
    return CapabilityReport(
        target=manifest.target,
        decisions=decisions,
        blocked_task_ids=blocked_tasks,
        diagnostics=diagnostics,
    )
