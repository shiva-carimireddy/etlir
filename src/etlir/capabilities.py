"""Target capability manifests and generic capability analysis.

A target emitter declares, per canonical construct, whether it is supported, constrained
(supported only when stated preconditions hold), approximated, or blocked. Constructs the
manifest does not mention are blocked: analysis fails closed.

Blocking propagates along the task graph: a task is blocked if its own kind or any
construct in its dataflow is blocked, or if any upstream task it depends on is blocked.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from etlir.canonical.invariants import validate, walk_expression
from etlir.canonical.model import CallNode, CanonicalDocument, CastNode, OpaqueNode, SourceRef
from etlir.evidence import Diagnostic, Severity


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CapabilityState(StrEnum):
    SUPPORTED = "supported"
    CONSTRAINED = "constrained"
    APPROXIMATED = "approximated"
    BLOCKED = "blocked"


class CapabilityRule(_Model):
    construct_id: str = Field(
        description="Construct key: 'operation.<kind>', 'write.<mode>', 'lookup.<policy>', "
        "'task.<kind>', "
        "'dependency.<condition>', 'trigger.any', 'function.<name>', 'cast.<type>' or "
        "'expression.opaque'."
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
    blocked_dataflow_ids: list[str]
    blocked_task_ids: list[str]
    diagnostics: list[Diagnostic]


def _state(manifest: CapabilityManifest, construct: str) -> CapabilityState:
    rule = manifest.lookup(construct)
    return rule.state if rule is not None else CapabilityState.BLOCKED


def constructs_of_expression(node: object) -> list[str]:
    out: list[str] = []
    for n in walk_expression(node):  # type: ignore[arg-type]
        if isinstance(n, CallNode):
            out.append(f"function.{n.function}")
        elif isinstance(n, CastNode):
            out.append(f"cast.{n.to.kind.value}")
        elif isinstance(n, OpaqueNode):
            out.append("expression.opaque")
    return list(dict.fromkeys(out))


def analyze(
    doc: CanonicalDocument,
    manifest: CapabilityManifest,
    extra_blocked: Mapping[str, list[str]] | None = None,
) -> CapabilityReport:
    """Decide every construct against ``manifest``.

    ``extra_blocked`` maps dataflow ids to reasons found by an emitter's own preflight
    (for example, unknown column types); those dataflows and their tasks are blocked too.
    """
    decisions: list[Decision] = []
    seen: set[tuple[str, str]] = set()

    def decide(subject: str, construct: str, source: SourceRef) -> CapabilityState:
        state = _state(manifest, construct)
        if (subject, construct) not in seen:
            seen.add((subject, construct))
            decisions.append(
                Decision(subject_id=subject, construct_id=construct, state=state, source=source)
            )
        return state

    blocked_dataflows: set[str] = set(extra_blocked or {})
    # A dataflow that violates a structural invariant is never emitted, whatever the target.
    errors = [d for d in validate(doc) if d.severity is Severity.ERROR]
    for df in doc.dataflows:
        members = {df.id, *(o.id for o in df.operations), *(e.id for e in df.expressions)}
        for d in errors:
            if d.subject_id in members:
                decide(d.subject_id, f"invariant.{d.code}", df.source)
                blocked_dataflows.add(df.id)
    for df in doc.dataflows:
        for op in df.operations:
            constructs = [f"operation.{op.spec.kind}"]
            if op.spec.kind == "write":
                constructs.append(f"write.{op.spec.mode.value}")
            elif op.spec.kind == "lookup":
                constructs.append(f"lookup.{op.spec.on_multiple_match}")
            for c in constructs:
                if decide(op.id, c, op.source) is CapabilityState.BLOCKED:
                    blocked_dataflows.add(df.id)
        for expr in df.expressions:
            for c in constructs_of_expression(expr.ast):
                if decide(expr.id, c, expr.source) is CapabilityState.BLOCKED:
                    blocked_dataflows.add(df.id)

    blocked_tasks: list[str] = []
    for pipeline in doc.pipelines:
        blocked: set[str] = set()
        for task in pipeline.tasks:
            constructs = [f"task.{task.kind.value}"]
            constructs += [f"dependency.{d.condition.value}" for d in task.depends_on]
            if task.trigger == "any":
                constructs.append("trigger.any")
            for c in constructs:
                if decide(task.id, c, task.source) is CapabilityState.BLOCKED:
                    blocked.add(task.id)
            if task.dataflow_id in blocked_dataflows:
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
    for df_id, reasons in sorted((extra_blocked or {}).items()):
        df_src = next((d.source for d in doc.dataflows if d.id == df_id), None)
        for reason in reasons:
            diagnostics.append(
                Diagnostic(
                    code="CAP-B-002",
                    severity=Severity.ERROR,
                    message=f"Preflight for target '{manifest.target}': {reason}",
                    subject_id=df_id,
                    source=df_src,
                )
            )
    return CapabilityReport(
        target=manifest.target,
        decisions=decisions,
        blocked_dataflow_ids=sorted(blocked_dataflows),
        blocked_task_ids=blocked_tasks,
        diagnostics=diagnostics,
    )
