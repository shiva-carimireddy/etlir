"""Normalization rules: PowerCenter Raw IR -> Canonical IR.

Unit mapping:

* SOURCE / TARGET definitions -> Dataset (+ Binding).
* A session together with its mapping -> one Dataflow (session settings such as write
  mode and SQL overrides change semantics, so a mapping used by two sessions yields two
  dataflows). A mapping used by no session yields one mapping-only dataflow.
* WORKFLOW -> Pipeline; TASKINSTANCE -> Task; WORKFLOWLINK -> Dependency.

Every rule id used in provenance is ``pc.*`` and documented in docs/sources/powercenter.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from etlir.canonical.functions import NUMERIC
from etlir.canonical.invariants import walk_expression
from etlir.canonical.model import (
    AggregateOp,
    Aggregation,
    Assignment,
    Binding,
    CallNode,
    CanonicalDocument,
    CastNode,
    Column,
    ColumnMapping,
    ColumnRefNode,
    DataEdge,
    Dataflow,
    Dataset,
    DatasetKind,
    DataType,
    Dependency,
    DependencyCondition,
    DeriveOp,
    Expression,
    ExpressionNode,
    FilterOp,
    JoinOp,
    LiteralNode,
    LookupOp,
    OpaqueNode,
    Operation,
    OperationSpec,
    OutputGroup,
    Parameter,
    ParameterRefNode,
    ParameterScope,
    Pipeline,
    ProjectOp,
    ReadOp,
    RouteGroup,
    RouteOp,
    SequenceOp,
    SourceRef,
    Task,
    TaskKind,
    TypeKind,
    UnsupportedOp,
    WriteMode,
    WriteOp,
)
from etlir.canonical.rowalign import fuse_row_aligned, rename_columns
from etlir.evidence import Diagnostic, EvidenceRecord, EvidenceStatus, Severity
from etlir.sources.powercenter.expression import (
    APPROXIMATION_NOTES,
    DIALECT,
    Env,
    Opaque,
    Translation,
    translate,
)
from etlir.sources.powercenter.sql import (
    Query,
    SqlUnsupported,
    Translator,
    dialect_for,
    parse_select,
    plan_select,
)
from etlir.sources.powercenter.symbols import Folder, RNode, SymbolTable, make_id
from etlir.sources.powercenter.types import native_type, port_type

ADAPTER_ID = "powercenter-xml"

CODES = {
    "PC-X-001": "Malformed XML.",
    "PC-X-002": "Forbidden XML construct (entity declaration or external reference).",
    "PC-X-003": "Input exceeds the size limit.",
    "PC-X-004": "Not a PowerCenter export (root is not POWERMART).",
    "PC-R-001": "Referenced definition not found in the loaded corpus group.",
    "PC-R-002": "Conflicting definitions share one name; references to it are ambiguous.",
    "PC-R-003": "Workflow link references an unknown task instance.",
    "PC-N-001": "Datatype not recognized; typed as unknown.",
    "PC-N-002": "Session disables high precision; PowerCenter computes decimals as double.",
    "PC-N-003": "No session defines the write mode; append is assumed.",
    "PC-N-004": "Schedule information is not translated.",
    "PC-N-005": "SQL override translated; it now runs outside the source database.",
    "PC-N-006": "Inputs from several row-aligned transformations were fused.",
}

_ERROR_DEFAULT = re.compile(r"^\s*ERROR\s*\(\s*'transformation error'\s*\)\s*$", re.I)
_LINK = re.compile(r"^\s*\$([A-Za-z0-9_]+)\.Status\s*=\s*(SUCCEEDED|FAILED)\s*$", re.I)
_SQ_OVERRIDES = ("Sql Query", "Source Filter", "User Defined Join", "Pre SQL", "Post SQL")
_SENSITIVE = re.compile(r"PASS|PWD|SECRET|TOKEN|CREDENTIAL", re.I)
_TASK_KINDS = {
    "Command": TaskKind.COMMAND,
    "Email": TaskKind.NOTIFY,
    "Event Wait": TaskKind.WAIT,
    "Timer": TaskKind.WAIT,
    "Decision": TaskKind.DECISION,
    "Assignment": TaskKind.ASSIGNMENT,
}


@dataclass
class Port:
    node: RNode
    name: str
    porttype: str
    type: DataType
    group: str
    ref_field: str
    expression: str
    exprtype: str
    default: str

    @property
    def is_input(self) -> bool:
        return "INPUT" in self.porttype

    @property
    def is_output(self) -> bool:
        return "OUTPUT" in self.porttype

    @property
    def is_variable(self) -> bool:
        return "VARIABLE" in self.porttype

    @property
    def is_master(self) -> bool:
        return "MASTER" in self.porttype


def _fields(node: RNode, tag: str) -> list[RNode]:
    """Elementary fields, flattening nested (COBOL group-item) field hierarchies."""
    out: list[RNode] = []
    for f in node.children(tag):
        nested = list(f.children(tag))
        out.extend(_fields(f, tag) if nested else [f])
    return out


def _ports(node: RNode) -> list[Port]:
    return [
        Port(
            node=f,
            name=f.name,
            porttype=f.get("PORTTYPE").upper(),
            type=port_type(f.get("DATATYPE"), f.get("PRECISION"), f.get("SCALE")),
            group=f.get("GROUP"),
            ref_field=f.get("REF_FIELD"),
            expression=f.get("EXPRESSION"),
            exprtype=f.get("EXPRESSIONTYPE"),
            default=f.get("DEFAULTVALUE"),
        )
        for f in node.children("TRANSFORMFIELD")
    ]


@dataclass
class Connector:
    from_inst: tuple[str, str]  # (instance name, transformation type)
    from_field: str
    to_inst: tuple[str, str]
    to_field: str


class Unsupported(Exception):
    """Raised by an operation builder; the instance becomes an UnsupportedOp."""

    def __init__(self, reason: str, status: EvidenceStatus = EvidenceStatus.UNSUPPORTED):
        super().__init__(reason)
        self.status = status


@dataclass
class Built:
    spec: OperationSpec
    outputs: list[OutputGroup]
    rule: str
    status: EvidenceStatus = EvidenceStatus.TRANSFORMED_EQUIVALENT
    notes: list[str] = field(default_factory=list)
    # Builders that expand into a subgraph (SQL overrides, lookup sources) add operations
    # and edges; consume_inputs means incoming connectors are already wired by the builder.
    extra_ops: list[Operation] = field(default_factory=list)
    extra_edges: list[DataEdge] = field(default_factory=list)
    consume_inputs: bool = False
    input_target: str | None = None  # connectors feed this op instead (e.g. lookup-call args)


@dataclass
class LookupParts:
    cond_text: str
    cond: Translation  # over the lookup's own port names
    kind: str
    status: EvidenceStatus
    note: str
    in_ports: list[Port]
    extra_ops: list[Operation]
    extra_edges: list[DataEdge]
    lk_ports: list[Port] = field(default_factory=list)  # lookup columns actually used


@dataclass
class LookupCall:
    k: int
    name: str
    definition: RNode
    column: str
    args: list[ExpressionNode]
    parts: LookupParts
    ret: Port


class LookupCalls:
    """Resolves ``:LKP.name(args)`` inside an Expression transformation.

    Each distinct call (same lookup, same arguments) becomes: arguments computed by a
    derive step, then a canonical lookup returning the lookup's return port as column
    ``__lkp<k>``, which the expression reads. Problems make only the calling expression
    opaque.
    """

    def __init__(
        self,
        make_parts: Callable[[RNode, str, list[Port], str, str], LookupParts],
        definitions: dict[tuple[str, str], RNode | None],
        oid: str,
    ) -> None:
        self.make_parts = make_parts
        self.definitions = {
            k[0].upper(): v for k, v in definitions.items() if k[1] == "Lookup Procedure"
        }
        self.names = {k[0].upper(): k[0] for k in definitions if k[1] == "Lookup Procedure"}
        self.oid = oid
        self.calls: dict[str, LookupCall] = {}

    def __call__(
        self, name: str, args: list[tuple[ExpressionNode, DataType]]
    ) -> tuple[ExpressionNode, DataType]:
        definition = self.definitions.get(name.upper())
        if definition is None:
            raise Opaque(f"lookup {name} is not in the mapping")
        ports = _ports(definition)
        in_ports = [p for p in ports if "INPUT" in p.porttype]
        rets = [p for p in ports if "RETURN" in p.porttype] or [
            p for p in ports if p.is_output and "LOOKUP" in p.porttype
        ]
        if len(rets) != 1:
            raise Opaque(f"lookup {name} has no single return port")
        if len(args) != len(in_ports):
            raise Opaque(f"lookup {name} takes {len(in_ports)} arguments, got {len(args)}")
        nodes: list[ExpressionNode] = []
        for (node, typ), port in zip(args, in_ports, strict=True):
            converted = convert(Translation(node, typ, set()), port.type, "")
            if isinstance(converted, OpaqueNode):
                raise Opaque(f"lookup {name} argument {port.name}: {converted.reason}")
            nodes.append(converted)
        key = name.upper() + "|" + "|".join(n.model_dump_json() for n in nodes)
        if key not in self.calls:
            k = len(self.calls) + 1
            lookup_id = make_id("op", f"lkp{k}", parent=self.oid)
            try:
                parts = self.make_parts(
                    definition, self.names[name.upper()], ports, lookup_id, f"lkp{k}."
                )
            except Unsupported as exc:
                raise Opaque(f"lookup {name}: {exc}") from exc
            if parts.kind == "all":
                raise Opaque(f"lookup {name} returns all matches (not allowed in expressions)")
            self.calls[key] = LookupCall(k, name, definition, f"__lkp{k}", nodes, parts, rets[0])
        call = self.calls[key]
        return ColumnRefNode(name=call.column), call.ret.type


@dataclass
class FlowCtx:
    op_id: Callable[[tuple[str, str]], str]
    definitions: dict[tuple[str, str], RNode | None]
    outgoing: dict[tuple[str, str], list[Connector]]
    # Row operations of the Update Strategy transformations upstream of each target.
    strategies: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    # Sequence Generator -> (pass-through columns, generated column) after splicing.
    sequences: dict[tuple[str, str], tuple[list[Column], str]] = field(default_factory=dict)


_SEQUENCE_TYPES = ("Sequence", "Sequence Generator")


_ROW_OPERATIONS = {
    "DD_INSERT": "insert",
    "0": "insert",
    "DD_UPDATE": "update",
    "1": "update",
    "DD_DELETE": "delete",
    "2": "delete",
    "DD_REJECT": "reject",
    "3": "reject",
}


def row_operation(definition: RNode) -> str:
    """The constant row operation of an Update Strategy, or 'expression'."""
    text = definition.attributes("TABLEATTRIBUTE").get("Update Strategy Expression", "")
    return _ROW_OPERATIONS.get(text.strip().upper(), "expression")


_LOOKUP_POLICY = {
    "use any value": ("any", EvidenceStatus.TRANSFORMED_EQUIVALENT, ""),
    "return all values": ("all", EvidenceStatus.TRANSFORMED_EQUIVALENT, ""),
    "use first value": (
        "error",
        EvidenceStatus.APPROXIMATED,
        "first-value choice depends on cache order; runs only if keys are unique",
    ),
    "use last value": (
        "error",
        EvidenceStatus.APPROXIMATED,
        "last-value choice depends on cache order; runs only if keys are unique",
    ),
    "report error": (
        "error",
        EvidenceStatus.APPROXIMATED,
        "PowerCenter reports the row; ETLIR fails the task on a duplicate match",
    ),
}


class Normalizer:
    def __init__(self, table: SymbolTable) -> None:
        self.table = table
        self.diagnostics: list[Diagnostic] = []
        self.evidence: list[EvidenceRecord] = []
        self.datasets: dict[str, Dataset] = {}
        self.bindings: dict[str, Binding] = {}
        self.parameters: dict[str, Parameter] = {}
        self.dataflows: dict[str, Dataflow] = {}
        self.pipelines: list[Pipeline] = []
        self.expr_notes: dict[str, set[str]] = {}

    # ------------------------------------------------------------------ helpers

    def ref(self, node: RNode, rule: str) -> SourceRef:
        return SourceRef(
            adapter=ADAPTER_ID,
            artifact=node.artifact,
            locator=node.locator,
            native_id=node.name or None,
            rule=rule,
        )

    def diag(
        self, code: str, severity: Severity, message: str, subject: str | None, src: SourceRef
    ) -> None:
        self.diagnostics.append(
            Diagnostic(
                code=code, severity=severity, message=message, subject_id=subject, source=src
            )
        )

    def record(
        self, subject: str, status: EvidenceStatus, src: SourceRef, codes: Iterable[str] = ()
    ) -> None:
        self.evidence.append(
            EvidenceRecord(
                subject_id=subject,
                status=status,
                rule=src.rule,
                source=src,
                diagnostic_codes=sorted(set(codes)),
            )
        )

    # ------------------------------------------------------------------ top level

    def run(self) -> CanonicalDocument:
        for folder in self.table.sorted_folders():
            self.folder_datasets(folder)
        for folder in self.table.sorted_folders():
            self.folder_flows(folder)
        used = {
            n.parameter_id
            for df in self.dataflows.values()
            for e in df.expressions
            for n in walk_expression(e.ast)
            if isinstance(n, ParameterRefNode)
        }
        return CanonicalDocument(
            pipelines=self.pipelines,
            dataflows=[self.dataflows[k] for k in sorted(self.dataflows)],
            datasets=[self.datasets[k] for k in sorted(self.datasets)],
            parameters=[
                self.parameters[k]
                for k in sorted(self.parameters)
                if not self.parameters[k].builtin or k in used
            ],
            bindings=[self.bindings[k] for k in sorted(self.bindings)],
        )

    # ------------------------------------------------------------------ datasets

    def dataset_id(self, folder: Folder, kind: str, *key: str) -> str:
        return make_id("ds", folder.repository, folder.name, kind, *key)

    def folder_datasets(self, folder: Folder) -> None:
        for kind, tag, field_tag in (
            ("src", "SOURCE", "SOURCEFIELD"),
            ("tgt", "TARGET", "TARGETFIELD"),
        ):
            for key, node in folder.items(tag):
                ds_id = self.dataset_id(folder, kind, *key)
                rule = f"pc.dataset.{kind}"
                src = self.ref(node, rule)
                cols: list[Column] = []
                for f in _fields(node, field_tag):
                    t = native_type(f.get("DATATYPE"), f.get("PRECISION"), f.get("SCALE"))
                    if t.kind is TypeKind.UNKNOWN:
                        self.diag(
                            "PC-N-001",
                            Severity.WARNING,
                            f"Unknown datatype '{f.get('DATATYPE')}' of {f.name}.",
                            ds_id,
                            self.ref(f, rule),
                        )
                    cols.append(Column(name=f.name, type=t))
                flat = node.child("FLATFILE")
                is_file = flat is not None or "flat file" in node.get("DATABASETYPE").lower()
                fmt = None
                if is_file:
                    fmt = "delimited" if flat is None or flat.get("DELIMITED") != "NO" else "fixed"
                bind_id = make_id("bind", folder.repository, folder.name, kind, *key)
                self.bindings[bind_id] = Binding(
                    id=bind_id,
                    name=node.name,
                    resource_kind="path" if is_file else "connection",
                    reference="/".join((folder.name, *key)),
                    source=src,
                )
                self.datasets[ds_id] = Dataset(
                    id=ds_id,
                    name=node.name,
                    kind=DatasetKind.FILE if is_file else DatasetKind.TABLE,
                    columns=cols,
                    binding_id=bind_id,
                    format=fmt,
                    source=src,
                )
                self.record(ds_id, EvidenceStatus.PRESERVED, src)
            for key in sorted(folder.conflicts.get(tag, set())):
                self.diag(
                    "PC-R-002",
                    Severity.ERROR,
                    f"Conflicting {tag} definitions named {'/'.join(key)}.",
                    None,
                    SourceRef(adapter=ADAPTER_ID, artifact="*", locator=f"{folder.name}"),
                )

    # ------------------------------------------------------------------ flows

    def folder_flows(self, folder: Folder) -> None:
        used_mappings: set[str] = set()
        sessions: list[tuple[tuple[str, ...], RNode]] = [
            ((s.name,), s) for _, s in folder.items("SESSION")
        ]
        for _, wf in folder.items("WORKFLOW"):
            sessions += [((wf.name, s.name), s) for s in wf.children("SESSION")]
        session_df: dict[tuple[str, ...], str | None] = {}
        for key, session in sessions:
            mapping_name = session.get("MAPPINGNAME")
            mapping = folder.get("MAPPING", mapping_name)
            if mapping is None:
                session_df[key] = None
                code = "PC-R-002" if folder.is_conflict("MAPPING", mapping_name) else "PC-R-001"
                self.diag(
                    code,
                    Severity.ERROR,
                    f"Session {session.name} references mapping '{mapping_name}', "
                    "which is not available in the loaded group.",
                    None,
                    self.ref(session, "pc.session"),
                )
                continue
            used_mappings.add(mapping_name)
            df_id = make_id("df", folder.repository, folder.name, *key)
            session_df[key] = df_id
            self.dataflow(folder, mapping, session, df_id, name=session.name)
        for (mapping_name,), mapping in folder.items("MAPPING"):
            if mapping_name not in used_mappings:
                df_id = make_id("df", folder.repository, folder.name, "mapping", mapping_name)
                self.dataflow(folder, mapping, None, df_id, name=mapping_name)
        for _, wf in folder.items("WORKFLOW"):
            self.pipeline(folder, wf, session_df)

    # ------------------------------------------------------------------ parameters

    def mapping_env(self, folder: Folder, mapping: RNode, session: RNode | None = None) -> Env:
        """Mapping parameters and variables become canonical run parameters: PowerCenter
        evaluates variable references with the start value for the whole session. Variables
        changed with SETVARIABLE (or its kin) are recorded so reads carry an approximation
        note (ETLIR does not persist the final value).

        Built-ins: SESSSTARTTIME and SYSDATE read the run-start-time parameter; the mapping,
        folder, repository and session names are literals."""
        env = Env()
        modified = self.modified_variables(mapping)
        start = make_id("prm", "builtin", "run_start_time")
        self.parameters.setdefault(
            start,
            Parameter(
                id=start,
                name="run_start_time",
                scope=ParameterScope.PIPELINE,
                type=DataType(kind=TypeKind.TIMESTAMP),
                builtin="run_start_time",
                source=self.ref(mapping, "pc.parameter.run-start-time"),
            ),
        )
        ts = DataType(kind=TypeKind.TIMESTAMP)
        string = DataType(kind=TypeKind.STRING)
        env.builtins = {
            "SESSSTARTTIME": (ParameterRefNode(parameter_id=start), ts),
            "SYSDATE": (ParameterRefNode(parameter_id=start), ts),
            "$PMMAPPINGNAME": (LiteralNode(value=mapping.name, type=string), string),
            "$PMFOLDERNAME": (LiteralNode(value=folder.name, type=string), string),
            "$PMREPOSITORYNAME": (LiteralNode(value=folder.repository, type=string), string),
        }

        def pm_parameter(name: str) -> tuple[ExpressionNode, DataType]:
            pid = make_id("prm", "environment", name.lstrip("$"))
            self.parameters.setdefault(
                pid,
                Parameter(
                    id=pid,
                    name=name,
                    scope=ParameterScope.ENVIRONMENT,
                    type=string,
                    source=self.ref(mapping, "pc.parameter.pm-variable"),
                ),
            )
            return ParameterRefNode(parameter_id=pid), string

        env.pm_parameter = pm_parameter
        if session is not None:
            env.builtins["$PMSESSIONNAME"] = (
                LiteralNode(value=session.name, type=string),
                string,
            )
        for v in mapping.children("MAPPINGVARIABLE"):
            key = v.name.lstrip("$").upper()
            if v.get("ISPARAM") != "YES" and key in modified:
                env.stateful.add(key)
            pid = make_id("prm", folder.repository, folder.name, mapping.name, v.name.lstrip("$"))
            sensitive = bool(_SENSITIVE.search(v.name))
            ptype = port_type(v.get("DATATYPE"), v.get("PRECISION"), v.get("SCALE"))
            self.parameters.setdefault(
                pid,
                Parameter(
                    id=pid,
                    name=v.name,
                    scope=ParameterScope.DATAFLOW,
                    type=ptype,
                    default=None
                    if sensitive or v.get("DEFAULTVALUE") == ""
                    else v.get("DEFAULTVALUE"),
                    sensitive=sensitive,
                    source=self.ref(
                        v,
                        "pc.parameter.mapping"
                        if v.get("ISPARAM") == "YES"
                        else "pc.parameter.mapping-variable",
                    ),
                ),
            )
            env.parameters[key] = (pid, ptype)
        return env

    @staticmethod
    def modified_variables(mapping: RNode) -> set[str]:
        pattern = re.compile(r"SET(?:MAX|MIN|COUNT)?VARIABLE\s*\(\s*\$\$(\w+)", re.I)
        found: set[str] = set()

        def scan(node: RNode) -> None:
            for f in node.children("TRANSFORMFIELD"):
                found.update(m.group(1).upper() for m in pattern.finditer(f.get("EXPRESSION")))
            for child in node.children("TRANSFORMATION"):
                scan(child)

        scan(mapping)
        return found

    # ------------------------------------------------------------------ dataflow

    def dataflow(
        self, folder: Folder, mapping: RNode, session: RNode | None, df_id: str, name: str
    ) -> None:
        df_src = self.ref(
            session or mapping, "pc.dataflow.session" if session else "pc.dataflow.mapping"
        )
        base_env = self.mapping_env(folder, mapping, session)
        local = {t.name: t for t in mapping.children("TRANSFORMATION")}
        # Instance names are unique per transformation type only (a source and its source
        # qualifier may share a name); connectors disambiguate with *INSTANCETYPE.
        instances = {
            (i.name, i.get("TRANSFORMATION_TYPE")): i for i in mapping.children("INSTANCE")
        }
        connectors = [
            Connector(
                (c.get("FROMINSTANCE"), c.get("FROMINSTANCETYPE")),
                c.get("FROMFIELD"),
                (c.get("TOINSTANCE"), c.get("TOINSTANCETYPE")),
                c.get("TOFIELD"),
            )
            for c in mapping.children("CONNECTOR")
        ]
        definitions: dict[tuple[str, str], RNode | None] = {}
        for key, inst in instances.items():
            definitions[key] = self.resolve_instance(folder, inst, local)
        connectors, sequences = self.splice_sequences(folder, connectors, definitions)

        incoming: dict[tuple[str, str], list[Connector]] = {}
        outgoing: dict[tuple[str, str], list[Connector]] = {}
        for c in connectors:
            incoming.setdefault(c.to_inst, []).append(c)
            outgoing.setdefault(c.from_inst, []).append(c)
        name_count: dict[str, int] = {}
        for inst_name, _ in instances:
            name_count[inst_name] = name_count.get(inst_name, 0) + 1

        def op_id(key: tuple[str, str]) -> str:
            if name_count.get(key[0], 0) > 1:
                return make_id("op", key[0], key[1], parent=df_id)
            return make_id("op", key[0], parent=df_id)

        # Upstream (group, column) of each connector's FROMFIELD (routers rename ports).
        def upstream(c: Connector) -> tuple[str, str]:
            d = definitions.get(c.from_inst)
            if d is not None and c.from_inst[1] == "Router":
                for p in _ports(d):
                    if p.name == c.from_field and p.is_output:
                        return p.group, p.ref_field or p.name
            return "out", c.from_field

        session_attrs = session.attributes() if session is not None else {}
        high_precision_off = (
            session is not None
            and session_attrs.get("Enable high precision", "NO").upper() != "YES"
        )

        operations: list[Operation] = []
        expressions: list[Expression] = []
        edges: list[DataEdge] = []
        native_kind: dict[str, str] = {}
        uses_decimal = False
        ctx = FlowCtx(op_id=op_id, definitions=definitions, outgoing=outgoing, sequences=sequences)
        for key in definitions:
            if key[1] != "Target Definition":
                continue
            seen: set[tuple[str, str]] = set()
            todo = [key]
            while todo:
                k = todo.pop()
                for c in incoming.get(k, []):
                    if c.from_inst in seen:
                        continue
                    seen.add(c.from_inst)
                    todo.append(c.from_inst)
                    d = definitions.get(c.from_inst)
                    if c.from_inst[1] == "Update Strategy" and d is not None:
                        ctx.strategies.setdefault(key, set()).add(row_operation(d))
        for inst_key, inst in instances.items():
            oid = op_id(inst_key)
            definition = definitions[inst_key]
            ttype = inst.get("TRANSFORMATION_TYPE")
            ins = incoming.get(inst_key, [])
            if ttype == "Lookup Procedure" and not ins and not outgoing.get(inst_key):
                continue  # unconnected lookup: a function, materialized at its call sites
            ups = sorted({(c.from_inst, upstream(c)[0]) for c in ins})
            anchor = (
                inst
                if ttype in ("Source Definition", "Target Definition")
                else (definition or inst)
            )
            try:
                if definition is None:
                    raise Unsupported(
                        f"definition of {ttype} '{inst.get('TRANSFORMATION_NAME')}' not found",
                        EvidenceStatus.MISSING_INFORMATION,
                    )
                built = self.build(
                    folder,
                    ttype,
                    inst,
                    definition,
                    ins,
                    ups,
                    session,
                    oid,
                    expressions,
                    base_env,
                    ctx,
                )
            except Unsupported as exc:
                outputs = self.unsupported_outputs(definition, outgoing.get(inst_key, []), upstream)
                built = Built(
                    UnsupportedOp(native_kind=ttype or inst.get("TYPE"), reason=str(exc)),
                    outputs,
                    "pc.op.unsupported",
                    exc.status,
                )
            src = self.ref(anchor, built.rule)
            native_kind[oid] = ttype or inst.get("TYPE")
            operations.append(Operation(id=oid, spec=built.spec, outputs=built.outputs, source=src))
            self.record(oid, built.status, src)
            for extra in built.extra_ops:
                operations.append(extra)
                self.record(extra.id, built.status, extra.source)
            edges.extend(built.extra_edges)
            uses_decimal = (
                uses_decimal
                or any(
                    c.type.kind is TypeKind.DECIMAL
                    for op in [*built.extra_ops]
                    for g in op.outputs
                    for c in g.columns
                )
                or any(c.type.kind is TypeKind.DECIMAL for g in built.outputs for c in g.columns)
            )
            if built.consume_inputs:
                continue

            # Edges: one per (upstream instance, group, slot).
            grouped: dict[tuple[tuple[str, str], str, str], list[ColumnMapping]] = {}
            unsupported = built.spec.kind == "unsupported"
            slot_of = self.slots(built, definition)
            for c in ins:
                if c.from_inst not in instances:
                    continue
                group, col = upstream(c)
                slot = slot_of.get(c.to_field, "in")
                if unsupported:
                    slot = f"in{ups.index((c.from_inst, group)) + 1}"
                grouped.setdefault((c.from_inst, group, slot), []).append(
                    ColumnMapping(from_column=col, to_column=c.to_field)
                )
            for (frm, group, slot), maps in sorted(grouped.items()):
                edges.append(
                    DataEdge(
                        from_operation=op_id(frm),
                        from_group=group,
                        to_operation=built.input_target or oid,
                        to_input=slot,
                        columns=maps,
                    )
                )

        status = EvidenceStatus.TRANSFORMED_EQUIVALENT
        codes: list[str] = []
        if high_precision_off and uses_decimal:
            status = EvidenceStatus.APPROXIMATED
            codes.append("PC-N-002")
            self.diag(
                "PC-N-002",
                Severity.WARNING,
                "High precision is disabled: PowerCenter processes decimal ports as "
                "double, while canonical decimals are exact.",
                df_id,
                df_src,
            )
        flow = Dataflow(
            id=df_id,
            name=name,
            operations=operations,
            edges=edges,
            expressions=expressions,
            source=df_src,
        )
        merges = {
            e.to_operation
            for e in edges
            if sum(
                1 for x in edges if x.to_operation == e.to_operation and x.to_input == e.to_input
            )
            > 1
        }
        flow, failures = fuse_row_aligned(flow, self.datasets)
        for failure in failures:
            flow = self.mark_unsupported(
                flow, failure.op_id, native_kind.get(failure.op_id, ""), str(failure)
            )
        for op in flow.operations:
            if op.id in merges and op.id not in {f.op_id for f in failures}:
                self.diag(
                    "PC-N-006",
                    Severity.INFO,
                    "Inputs from several row-aligned transformations were fused into "
                    "one row stream.",
                    op.id,
                    op.source,
                )
        self.dataflows[df_id] = flow
        self.record(df_id, status, df_src, codes)
        for e in expressions:
            est = EvidenceStatus.TRANSFORMED_EQUIVALENT
            if isinstance(e.ast, OpaqueNode):
                est = EvidenceStatus.UNSUPPORTED
            elif self.expr_notes.get(e.id, set()) & set(APPROXIMATION_NOTES):
                est = EvidenceStatus.APPROXIMATED
            self.record(e.id, est, e.source)

    def resolve_instance(
        self, folder: Folder, inst: RNode, local: dict[str, RNode]
    ) -> RNode | None:
        kind, tname = inst.get("TYPE"), inst.get("TRANSFORMATION_NAME")
        if kind == "SOURCE":
            return folder.get("SOURCE", inst.get("DBDNAME"), tname)
        if kind == "TARGET":
            return folder.get("TARGET", tname)
        if kind == "TRANSFORMATION":
            if inst.get("REUSABLE") == "YES":
                return folder.get("TRANSFORMATION", tname)
            return local.get(tname)
        if kind == "MAPPLET":
            return folder.get("MAPPLET", tname)
        return None

    def slots(self, built: Built, definition: RNode | None) -> dict[str, str]:
        if built.spec.kind != "join" or definition is None:
            return {}
        return {p.name: ("right" if p.is_master else "left") for p in _ports(definition)}

    def unsupported_outputs(
        self,
        definition: RNode | None,
        out: list[Connector],
        upstream: Callable[[Connector], tuple[str, str]],
    ) -> list[OutputGroup]:
        """Outputs of an unsupported operation, as far as downstream connectors reveal."""
        types = (
            {p.name: p.type for p in _ports(definition)}
            if definition is not None and definition.tag == "TRANSFORMATION"
            else {}
        )
        unknown = DataType(kind=TypeKind.UNKNOWN)
        groups: dict[str, dict[str, DataType]] = {}
        for c in out:
            group, col = upstream(c)
            groups.setdefault(group, {}).setdefault(col, types.get(col, unknown))
        return [
            OutputGroup(name=g, columns=[Column(name=n, type=t) for n, t in cols.items()])
            for g, cols in groups.items()
        ]

    # ------------------------------------------------------------------ operations

    def build(
        self,
        folder: Folder,
        ttype: str,
        inst: RNode,
        definition: RNode,
        ins: list[Connector],
        ups: list[tuple[tuple[str, str], str]],
        session: RNode | None,
        oid: str,
        expressions: list[Expression],
        base_env: Env,
        ctx: FlowCtx,
    ) -> Built:
        # Several upstreams into one non-join transformation are row-aligned merges; they
        # are fused after the dataflow is built (or rejected there if not aligned).
        if ttype == "Source Definition":
            return self.build_read(folder, definition)
        if not ins and ttype not in ("Source Definition", "Source Qualifier", *_SEQUENCE_TYPES):
            # Nothing flows in: the number of rows is undefined (or nothing is written).
            raise Unsupported("no connected inputs")
        if ttype == "Target Definition":
            strategies = ctx.strategies.get((inst.name, ttype), set())
            return self.build_write(folder, inst, definition, session, oid, strategies)
        if definition.tag == "MAPPLET":
            raise Unsupported("mapplet expansion is not supported")

        ports = _ports(definition)
        connected = {c.to_field for c in ins}
        if ttype in ("Source Qualifier", "Filter", "Router", "Joiner", "Sorter"):
            # A port that receives nothing and passes nothing on carries no data; drop it.
            # (Conditions that still read it fail closed as unknown ports.)
            used = {c.from_field for c in ctx.outgoing.get((inst.name, ttype), [])}
            # Router group outputs name their input port in REF_FIELD.
            used |= {p.node.get("REF_FIELD") for p in ports if p.name in used}
            dropped = {
                p.name
                for p in ports
                if p.is_input and p.name not in connected and p.name not in used
            }
            ports = [
                p for p in ports if p.name not in dropped and p.node.get("REF_FIELD") not in dropped
            ]

        def add_expr(
            suffix: str, text: str, t: Translation, anchor: RNode, dialect: str = DIALECT
        ) -> str:
            ex_id = make_id("ex", suffix, parent=oid)
            rule = "pc.expr.opaque" if t.opaque else ("pc.sql" if dialect != DIALECT else "pc.expr")
            self.expr_notes[ex_id] = set(t.notes)
            expressions.append(
                Expression(
                    id=ex_id,
                    ast=t.node,
                    original_text=text,
                    original_dialect=dialect,
                    result_type=t.type,
                    source=self.ref(anchor, rule),
                )
            )
            return ex_id

        def env_for(inputs: list[Port], **kw: object) -> Env:
            env = Env(
                parameters=dict(base_env.parameters),
                stateful=set(base_env.stateful),
                builtins=dict(base_env.builtins),
                pm_parameter=base_env.pm_parameter,
            )
            for k, v in kw.items():
                setattr(env, k, v)
            for p in inputs:
                env.columns[p.name] = p.type
            return env

        if ttype == "Source Qualifier":
            return self.build_sq(
                folder, inst, definition, ports, ins, session, oid, ctx, add_expr, base_env
            )
        if ttype == "Lookup Procedure":
            return self.build_lookup(
                folder, inst, definition, ports, ins, session, oid, add_expr, env_for, base_env
            )
        if ttype == "Expression":

            def make_parts(
                d: RNode, name: str, lk_ports_all: list[Port], lookup_id: str, prefix: str
            ) -> LookupParts:
                return self.lookup_parts(
                    folder,
                    name,
                    d,
                    lk_ports_all,
                    session,
                    lookup_id,
                    add_expr,
                    env_for,
                    base_env,
                    prefix,
                )

            calls = LookupCalls(make_parts, ctx.definitions, oid)
            return self.build_expression(ports, connected, env_for, add_expr, definition, calls)
        if ttype in _SEQUENCE_TYPES:
            return self.build_sequence(folder, definition, (inst.name, ttype), ctx)
        if ttype == "Update Strategy":
            # Rows pass unchanged; a constant row operation is applied by the session at
            # the target (see build_write). Row-level expressions are not supported.
            if row_operation(definition) == "expression":
                raise Unsupported("row-level update strategy expression")
            self.require_connected(ports, connected)
            cols = [Column(name=p.name, type=p.type) for p in ports if p.is_output]
            return Built(
                ProjectOp(columns=[c.name for c in cols]),
                [OutputGroup(columns=cols)],
                "pc.op.update-strategy",
            )
        if ttype == "Filter":
            self.require_connected(ports, connected)
            cond = definition.attributes("TABLEATTRIBUTE").get("Filter Condition", "") or "TRUE"
            t = as_condition(translate(cond, env_for([p for p in ports if p.is_input])), cond)
            ex = add_expr("condition", cond, t, definition)
            return Built(
                FilterOp(predicate_expression_id=ex),
                [
                    OutputGroup(
                        columns=[Column(name=p.name, type=p.type) for p in ports if p.is_output]
                    )
                ],
                "pc.op.filter",
            )
        if ttype == "Router":
            return self.build_router(definition, ports, connected, env_for, add_expr)
        if ttype == "Joiner":
            return self.build_joiner(definition, ports, connected, env_for, add_expr)
        if ttype == "Aggregator":
            return self.build_aggregator(ports, connected, env_for, add_expr, definition, oid)
        raise Unsupported(f"transformation type '{ttype}' is not supported")

    @staticmethod
    def require_connected(ports: list[Port], connected: set[str]) -> None:
        missing = [p.name for p in ports if p.is_input and p.name not in connected]
        if missing:
            raise Unsupported(f"unconnected input ports {missing}")

    def build_read(self, folder: Folder, definition: RNode) -> Built:
        dbtype = definition.get("DATABASETYPE")
        if dbtype.upper() in ("VSAM", "IMS") or any(
            f.get("FIELDTYPE") == "GRPITEM" for f in definition.children("SOURCEFIELD")
        ):
            raise Unsupported(
                f"hierarchical ({dbtype or 'COBOL'}) source layouts are not supported"
            )
        ds_id = self.dataset_id(folder, "src", definition.get("DBDNAME"), definition.name)
        if ds_id not in self.datasets:
            raise Unsupported("source definition is ambiguous", EvidenceStatus.AMBIGUOUS)
        return Built(ReadOp(dataset_id=ds_id), [], "pc.op.read", EvidenceStatus.PRESERVED)

    def build_write(
        self,
        folder: Folder,
        inst: RNode,
        definition: RNode,
        session: RNode | None,
        oid: str,
        strategies: set[str] | None = None,
    ) -> Built:
        ds_id = self.dataset_id(folder, "tgt", definition.name)
        if ds_id not in self.datasets:
            raise Unsupported("target definition is ambiguous", EvidenceStatus.AMBIGUOUS)
        if session is None:
            self.diag(
                "PC-N-003",
                Severity.INFO,
                "Write mode assumed to be append.",
                oid,
                self.ref(inst, "pc.op.write"),
            )
            return Built(
                WriteOp(dataset_id=ds_id, mode=WriteMode.APPEND),
                [],
                "pc.op.write",
                EvidenceStatus.MISSING_INFORMATION,
            )
        treat = session.attributes().get("Treat source rows as", "Insert").lower()
        if treat not in ("insert", "data driven"):
            raise Unsupported(f"session treats source rows as '{treat}' (update strategy)")
        # An insert session inserts every row whatever the Update Strategy says; a
        # data-driven one applies the (constant) row operation set upstream.
        operations = (strategies or {"insert"}) if treat == "data driven" else {"insert"}
        if len(operations) > 1 or operations & {"delete", "reject", "expression"}:
            raise Unsupported(f"row operations {sorted(operations)} at one target")
        writer: dict[str, str] = {}
        for ext in session.children("SESSIONEXTENSION"):
            if ext.get("SINSTANCENAME") == inst.name and ext.get("TYPE") == "WRITER":
                writer = {**ext.attributes(), "__file": str("File Writer" in ext.get("SUBTYPE"))}
        if operations == {"update"}:
            return self.keyed_write(ds_id, definition, writer)
        mode = WriteMode.APPEND
        for ext in session.children("SESSIONEXTENSION"):
            if ext.get("SINSTANCENAME") != inst.name or ext.get("TYPE") != "WRITER":
                continue
            attrs = ext.attributes()
            if "File Writer" in ext.get("SUBTYPE") or "File Writer" in ext.get("NAME"):
                mode = (
                    WriteMode.APPEND
                    if attrs.get("Append if Exists") == "YES"
                    else WriteMode.OVERWRITE
                )
            elif attrs.get("Truncate target table option", "NO") == "YES":
                mode = WriteMode.OVERWRITE
        return Built(
            WriteOp(dataset_id=ds_id, mode=mode), [], "pc.op.write", EvidenceStatus.PRESERVED
        )

    def splice_sequences(
        self,
        folder: Folder,
        connectors: list[Connector],
        definitions: dict[tuple[str, str], RNode | None],
    ) -> tuple[list[Connector], dict[tuple[str, str], tuple[list[Column], str]]]:
        """A Sequence Generator has no input: its NEXTVAL numbers the rows of the
        transformation it feeds. Route that consumer's other input through the generator,
        so the generator becomes a pass-through op that adds the number (a canonical
        sequence op) and the consumer has a single upstream. Shapes outside this (several
        consumers, CURRVAL, several other upstreams) are left alone and fail closed."""
        spliced: dict[tuple[str, str], tuple[list[Column], str]] = {}
        for key, d in definitions.items():
            if key[1] not in _SEQUENCE_TYPES or d is None:
                continue
            outs = [c for c in connectors if c.from_inst == key]
            consumers = {c.to_inst for c in outs}
            if len(outs) != 1 or outs[0].from_field != "NEXTVAL" or len(consumers) != 1:
                continue
            (consumer,) = consumers
            feeding = [c for c in connectors if c.to_inst == consumer and c.from_inst != key]
            if len({c.from_inst for c in feeding}) != 1:
                continue
            cdef = definitions.get(consumer)
            if cdef is None:
                continue
            types = self.port_types(folder, consumer, cdef)
            if any(c.to_field not in types for c in feeding):
                continue
            seq_col = outs[0].to_field
            cols = [Column(name=c.to_field, type=types[c.to_field]) for c in feeding]
            spliced[key] = (cols, seq_col)
            rest = [c for c in connectors if c not in feeding and c is not outs[0]]
            connectors = [
                *rest,
                *(Connector(c.from_inst, c.from_field, key, c.to_field) for c in feeding),
                *(Connector(key, c.to_field, consumer, c.to_field) for c in feeding),
                Connector(key, seq_col, consumer, seq_col),
            ]
        return connectors, spliced

    def port_types(
        self, folder: Folder, key: tuple[str, str], definition: RNode
    ) -> dict[str, DataType]:
        if key[1] == "Target Definition":
            ds_id = self.dataset_id(folder, "tgt", definition.name)
            ds = self.datasets.get(ds_id)
            return {c.name: c.type for c in ds.columns} if ds else {}
        return {p.name: p.type for p in _ports(definition) if p.is_input}

    def build_sequence(
        self, folder: Folder, definition: RNode, key: tuple[str, str], ctx: FlowCtx
    ) -> Built:
        if key not in ctx.sequences:
            raise Unsupported(
                "sequence generator shape not supported (NEXTVAL must feed one transformation "
                "that has one other upstream; CURRVAL is not supported)"
            )
        attrs = definition.attributes("TABLEATTRIBUTE")
        if attrs.get("Cycle", "NO").upper() == "YES":
            raise Unsupported("cycling sequence generator")
        if attrs.get("End Value", "9223372036854775807") != "9223372036854775807":
            raise Unsupported("sequence generator with an end value")
        increment = int(attrs.get("Increment By", "1") or "1")
        reset = attrs.get("Reset", "NO").upper() == "YES"
        start = attrs.get("Start Value" if reset else "Current Value", "") or attrs.get(
            "Start Value", "1"
        )
        pid = make_id("prm", folder.repository, folder.name, "sequence", definition.name)
        self.parameters.setdefault(
            pid,
            Parameter(
                id=pid,
                name=f"{definition.name}.start",
                scope=ParameterScope.DATAFLOW,
                type=DataType(kind=TypeKind.BIGINT),
                default=start,
                source=self.ref(definition, "pc.parameter.sequence-start"),
            ),
        )
        cols, column = ctx.sequences[key]
        out = [*cols, Column(name=column, type=DataType(kind=TypeKind.BIGINT))]
        return Built(
            SequenceOp(column=column, start_parameter_id=pid, increment=increment),
            [OutputGroup(columns=out)],
            "pc.op.sequence",
            EvidenceStatus.APPROXIMATED,
            [
                "values are assigned in a deterministic order (all columns ascending), not "
                "PowerCenter's arrival order; the current value is not persisted between runs "
                "(supply the start value as a run parameter)"
            ],
        )

    @staticmethod
    def keyed_write(ds_id: str, definition: RNode, writer: dict[str, str]) -> Built:
        """DD_UPDATE rows: 'Update as Update' updates by primary key, 'Update else Insert'
        upserts, 'Update as Insert' inserts."""
        if writer.get("__file") == "True":
            raise Unsupported("update strategy on a flat-file target")
        if writer.get("Update as Insert") == "YES":
            return Built(WriteOp(dataset_id=ds_id), [], "pc.op.write", EvidenceStatus.PRESERVED)
        if writer.get("Truncate target table option") == "YES":
            raise Unsupported("update strategy on a target truncated before the load")
        keys = [f.name for f in definition.children("TARGETFIELD") if "PRIMARY" in f.get("KEYTYPE")]
        if not keys:
            raise Unsupported("update strategy on a target without a primary key")
        mode = WriteMode.UPSERT if writer.get("Update else Insert") == "YES" else WriteMode.UPDATE
        return Built(
            WriteOp(dataset_id=ds_id, mode=mode, keys=keys),
            [],
            "pc.op.write-keyed",
            EvidenceStatus.PRESERVED,
        )

    @staticmethod
    def session_overrides(
        session: RNode | None, inst_name: str, names: tuple[str, ...]
    ) -> dict[str, str]:
        found: dict[str, str] = {}
        if session is None:
            return found
        for node in [
            *session.children("SESSTRANSFORMATIONINST"),
            *session.children("SESSIONEXTENSION"),
        ]:
            if node.get("SINSTANCENAME") == inst_name:
                for k, v in node.attributes().items():
                    if k in names and v.strip():
                        found[k] = v
        return found

    def build_sq(
        self,
        folder: Folder,
        inst: RNode,
        definition: RNode,
        ports: list[Port],
        ins: list[Connector],
        session: RNode | None,
        oid: str,
        ctx: FlowCtx,
        add_expr: Callable[..., str],
        base_env: Env,
    ) -> Built:
        attrs = definition.attributes("TABLEATTRIBUTE")
        ov = {k: attrs.get(k, "") for k in _SQ_OVERRIDES}
        ov |= self.session_overrides(session, inst.name, _SQ_OVERRIDES)
        if ov["Pre SQL"].strip() or ov["Post SQL"].strip():
            raise Unsupported("pre/post SQL statements have side effects and are not executed")
        distinct = attrs.get("Select Distinct", "NO") == "YES"
        sources = sorted({c.from_inst for c in ins})
        if any(k[1] != "Source Definition" for k in sources):
            raise Unsupported("source qualifier fed by a non-source transformation")
        connected = {c.to_field for c in ins}
        uses_sql = any(ov[k].strip() for k in ("Sql Query", "Source Filter", "User Defined Join"))
        if not uses_sql and not distinct:
            if len(sources) > 1:
                raise Unsupported(
                    "multi-source qualifier without a user-defined join (key-based default join)"
                )
            self.require_connected(ports, connected)
            cols = [Column(name=p.name, type=p.type) for p in ports if p.is_output]
            return Built(
                ProjectOp(columns=[c.name for c in cols]),
                [OutputGroup(columns=cols)],
                "pc.op.source-qualifier",
                EvidenceStatus.PRESERVED,
            )
        # SQL path: tables are the associated source definitions.
        tables: dict[str, tuple[str, str]] = {}  # upper name -> (read op id, dataset id)
        dbtypes = []
        for key in sources:
            d = ctx.definitions.get(key)
            if d is None:
                raise Unsupported("source definition not found", EvidenceStatus.MISSING_INFORMATION)
            ds_id = self.dataset_id(folder, "src", d.get("DBDNAME"), d.name)
            tables[d.name.upper()] = (ctx.op_id(key), ds_id)
            dbtypes.append(d.get("DATABASETYPE"))
        dialect = dialect_for(dbtypes[0]) if dbtypes else ""
        out_ports = [p for p in ports if p.is_output]
        try:
            if ov["Sql Query"].strip():
                query = plan_select(
                    parse_select(ov["Sql Query"], dialect), lambda n: self.table_columns(tables, n)
                )
                items = query.items
                if len(items) != len(out_ports):
                    wired = {
                        c.from_field for c in ctx.outgoing.get((inst.name, "Source Qualifier"), [])
                    }
                    out_ports = [p for p in out_ports if p.name in wired]
                if len(items) != len(out_ports):
                    raise SqlUnsupported(
                        f"query returns {len(items)} columns for {len(out_ports)} ports"
                    )
                text = ov["Sql Query"]
            else:
                where = " AND ".join(
                    f"({ov[k]})" for k in ("User Defined Join", "Source Filter") if ov[k].strip()
                )
                names = [ctx.definitions[k].name for k in sources]  # type: ignore[union-attr]
                synthetic = f"SELECT 1 FROM {', '.join(names)}" + (
                    f" WHERE {where}" if where else ""
                )
                query = plan_select(
                    parse_select(synthetic, dialect), lambda n: self.table_columns(tables, n)
                )
                text = synthetic
                by_port: dict[str, tuple[str, str]] = {}
                for c in ins:
                    d = ctx.definitions[c.from_inst]
                    assert d is not None
                    by_port[c.to_field] = (d.name, c.from_field)
                self.require_connected(ports, connected)
                from sqlglot import exp as sqlexp

                items = [
                    (p.name, sqlexp.column(by_port[p.name][1], table=by_port[p.name][0]))
                    for p in out_ports
                ]
            ops, edges = self.sql_relation(
                query,
                tables,
                [(p.name, p.type, e) for p, (_, e) in zip(out_ports, items, strict=True)],
                distinct or query.distinct,
                oid,
                definition,
                add_expr,
                base_env,
                dialect,
            )
        except SqlUnsupported as exc:
            raise Unsupported(f"SQL override: {exc}") from exc
        self.diag(
            "PC-N-005",
            Severity.INFO,
            f"SQL ({dialect or 'generic'} dialect) translated to canonical operations; "
            "it now runs outside the source database (binary collation assumed).",
            oid,
            self.ref(definition, "pc.sql"),
        )
        main = ops[-1]
        return Built(
            main.spec,
            main.outputs,
            "pc.op.source-qualifier.sql",
            EvidenceStatus.APPROXIMATED,
            notes=[text[:200]],
            extra_ops=ops[:-1],
            extra_edges=edges,
            consume_inputs=True,
        )

    def table_columns(
        self, tables: dict[str, tuple[str, str]], name: str
    ) -> dict[str, DataType] | None:
        entry = tables.get(name.upper())
        if entry is None:
            return None
        return {c.name: c.type for c in self.datasets[entry[1]].columns}

    def sql_relation(
        self,
        query: Query,
        tables: dict[str, tuple[str, str]],
        outputs: Sequence[tuple[str, DataType, Any]],
        distinct: bool,
        final_id: str,
        anchor: RNode,
        add_expr: Callable[..., str],
        base_env: Env,
        dialect: str,
        prefix: str = "",
    ) -> tuple[list[Operation], list[DataEdge]]:
        """Canonical ops for a planned query; the last op has id ``final_id``."""
        from sqlglot import exp as sqlexp

        tr = Translator(query, base_env.parameters, dialect)
        dialect_tag = f"sql:{dialect or 'generic'}"
        ops: list[Operation] = []
        edges: list[DataEdge] = []

        def op(suffix: str, spec: OperationSpec, cols: list[Column], rule: str) -> str:
            oid = make_id("op", *suffix.split("/"), parent=final_id)
            ops.append(
                Operation(
                    id=oid,
                    spec=spec,
                    outputs=[OutputGroup(columns=cols)],
                    source=self.ref(anchor, rule),
                )
            )
            return oid

        def expr(name: str, node: sqlexp.Expression, boolean: bool) -> str:
            ast, typ = tr.bool_(node) if boolean else tr.expr(node)
            t = Translation(ast, typ, {"pc.sql"})
            return add_expr(
                prefix + name, node.sql(dialect=dialect or None), t, anchor, dialect_tag
            )

        current: str | None = None
        current_cols: list[Column] = []
        for i, tbl in enumerate(query.tables):
            read_id, ds_id = tables[tbl.name.upper()]
            cols = [
                Column(name=f"{tbl.alias}.{c.name}", type=c.type)
                for c in self.datasets[ds_id].columns
            ]
            q_id = op(
                f"sql/{tbl.alias}", ProjectOp(columns=[c.name for c in cols]), cols, "pc.sql.table"
            )
            edges.append(
                DataEdge(
                    from_operation=read_id,
                    to_operation=q_id,
                    columns=[
                        ColumnMapping(from_column=c.name, to_column=f"{tbl.alias}.{c.name}")
                        for c in self.datasets[ds_id].columns
                    ],
                )
            )
            if i == 0:
                current, current_cols = q_id, cols
                continue
            kind, cond = query.joins[i - 1]
            joined = current_cols + cols
            j_id = op(
                f"sql/join/{tbl.alias}",
                JoinOp(
                    join_type=kind,
                    condition_expression_id=expr(f"sql.join.{tbl.alias}", cond, True),
                ),
                joined,
                "pc.sql.join",
            )
            edges.append(DataEdge(from_operation=current or "", to_operation=j_id, to_input="left"))
            edges.append(DataEdge(from_operation=q_id, to_operation=j_id, to_input="right"))
            current, current_cols = j_id, joined
        if query.filters:
            where: sqlexp.Expression = query.filters[0]
            for extra in query.filters[1:]:
                where = cast(sqlexp.Expression, sqlexp.and_(where, extra))
            f_id = op(
                "sql/where",
                FilterOp(predicate_expression_id=expr("sql.where", where, True)),
                current_cols,
                "pc.sql.where",
            )
            edges.append(DataEdge(from_operation=current or "", to_operation=f_id))
            current = f_id
        assignments = []
        out_cols = []
        if query.aggregate:
            keys: set[str] = set()
            for g in query.group_by:
                if isinstance(g, sqlexp.Column):
                    node0, _ = tr.column(g)
                    assert isinstance(node0, ColumnRefNode)
                    keys.add(node0.name)
            tr.group_keys = keys
        for name, typ, node in outputs:
            assert isinstance(node, sqlexp.Expression)
            ast, found = tr.expr(node)
            converted = convert(Translation(ast, found, set()), typ, node.sql())
            if isinstance(converted, OpaqueNode):
                raise SqlUnsupported(f"{name}: {converted.reason}")
            ex_id = add_expr(
                f"{prefix}sql.select.{name}",
                node.sql(dialect=dialect or None),
                Translation(converted, typ, {"pc.sql"}),
                anchor,
                dialect_tag,
            )
            assignments.append(Assignment(column=name, expression_id=ex_id))
            out_cols.append(Column(name=name, type=typ))
        select_id = final_id if not distinct else make_id("op", "sql", "select", parent=final_id)
        select_spec: OperationSpec = DeriveOp(assignments=assignments)
        if query.aggregate:
            select_spec = AggregateOp(
                group_by=sorted(tr.group_keys or set()),
                aggregations=[
                    Aggregation(column=a.column, expression_id=a.expression_id) for a in assignments
                ],
            )
        ops.append(
            Operation(
                id=select_id,
                spec=select_spec,
                outputs=[OutputGroup(columns=out_cols)],
                source=self.ref(anchor, "pc.sql.select"),
            )
        )
        edges.append(DataEdge(from_operation=current or "", to_operation=select_id))
        if distinct:
            ops.append(
                Operation(
                    id=final_id,
                    spec=AggregateOp(group_by=[c.name for c in out_cols], aggregations=[]),
                    outputs=[OutputGroup(columns=out_cols)],
                    source=self.ref(anchor, "pc.sql.distinct"),
                )
            )
            edges.append(DataEdge(from_operation=select_id, to_operation=final_id))
        return ops, edges

    # ------------------------------------------------------------------ lookup

    def lookup_table(self, folder: Folder, name: str) -> tuple[str, RNode] | None:
        """A source or target definition named like the lookup table."""
        for kind, tag in (("src", "SOURCE"), ("tgt", "TARGET")):
            for key, node in folder.items(tag):
                if key[-1].upper() == name.upper():
                    ds_id = self.dataset_id(folder, kind, *key)
                    if ds_id in self.datasets:
                        return ds_id, node
        return None

    def lookup_parts(
        self,
        folder: Folder,
        inst_name: str,
        definition: RNode,
        ports: list[Port],
        session: RNode | None,
        lookup_id: str,
        add_expr: Callable[..., str],
        env_for: Callable[..., Env],
        base_env: Env,
        prefix: str,
    ) -> LookupParts:
        """Shared by connected lookups and unconnected lookup calls: policy, condition,
        and the lookup-source subgraph wired into ``lookup_id``'s ``lookup`` slot."""
        names = ("Lookup Sql Override", "Lookup Source Filter", "Lookup table name")
        attrs = definition.attributes("TABLEATTRIBUTE")
        attrs |= self.session_overrides(session, inst_name, names)
        if attrs.get("Dynamic Lookup Cache", "NO") == "YES":
            raise Unsupported("dynamic lookup cache (the lookup changes while rows flow)")
        flat = attrs.get("Source Type", "Database").lower().startswith("flat")
        if flat and attrs.get("Case Sensitive String Comparison", "YES") == "NO":
            raise Unsupported("case-insensitive flat-file lookup")
        policy_text = (attrs.get("Lookup policy on multiple match") or "").lower()
        policy = next((v for k, v in _LOOKUP_POLICY.items() if policy_text.startswith(k)), None)
        if policy is None:
            policy = (
                "error",
                EvidenceStatus.APPROXIMATED,
                f"policy '{policy_text or 'unset'}'; runs only if keys are unique",
            )
        for p in ports:
            if p.default.strip() and not _ERROR_DEFAULT.match(p.default):
                raise Unsupported(f"port {p.name} has a default value (no-match replacement)")
        in_ports = [p for p in ports if "INPUT" in p.porttype]
        lk_ports = [p for p in ports if "LOOKUP" in p.porttype]
        if not lk_ports:
            raise Unsupported("lookup without lookup ports")
        cond_text = attrs.get("Lookup condition", "").strip()
        if not cond_text:
            raise Unsupported("lookup without a condition")
        cond = as_condition(translate(cond_text, env_for(in_ports + lk_ports)), cond_text)
        if cond.opaque:
            raise Unsupported(f"lookup condition: {getattr(cond.node, 'reason', '')}")
        # Only lookup ports used by the condition or returned matter; others (for example
        # ports an override does not select) cannot affect the result and are dropped.
        used = {n.name for n in walk_expression(cond.node) if isinstance(n, ColumnRefNode)}
        lk_ports = [p for p in lk_ports if p.name in used or p.is_output]

        table = (attrs.get("Lookup table name") or definition.name).strip()
        override = attrs.get("Lookup Sql Override", "").strip()
        sfilter = attrs.get("Lookup Source Filter", "").strip()
        extra_ops: list[Operation] = []
        extra_edges: list[DataEdge] = []
        existing = self.lookup_table(folder, table)
        lk_names = {p.name.upper(): p for p in lk_ports}
        read_id = make_id("op", "lookup-read", parent=lookup_id)
        try:
            if override or sfilter:
                if existing is None:
                    raise SqlUnsupported(f"table {table} has no source/target definition")
                ds_id, tnode = existing
                dialect = dialect_for(tnode.get("DATABASETYPE"))
                extra_ops.append(
                    Operation(
                        id=read_id,
                        spec=ReadOp(dataset_id=ds_id),
                        source=self.ref(definition, "pc.lookup.read"),
                    )
                )
                tables = {tnode.name.upper(): (read_id, ds_id)}
                from sqlglot import exp as sqlexp

                if override:
                    query = plan_select(
                        parse_select(override, dialect), lambda n: self.table_columns(tables, n)
                    )
                    by_alias = {(a or "").upper(): e for a, e in query.items}
                    missing = [p.name for p in lk_ports if p.name.upper() not in by_alias]
                    if missing:
                        raise SqlUnsupported(f"override does not return lookup ports {missing}")
                    outputs: list[tuple[str, DataType, Any]] = [
                        (p.name, p.type, by_alias[p.name.upper()]) for p in lk_ports
                    ]
                    distinct = query.distinct
                else:
                    query = plan_select(
                        parse_select(f"SELECT 1 FROM {tnode.name} WHERE {sfilter}", dialect),
                        lambda n: self.table_columns(tables, n),
                    )
                    cols = {c.name.upper(): c.name for c in self.datasets[ds_id].columns}
                    missing = [p.name for p in lk_ports if p.name.upper() not in cols]
                    if missing:
                        raise SqlUnsupported(f"table {table} lacks lookup ports {missing}")
                    outputs = [
                        (p.name, p.type, sqlexp.column(cols[p.name.upper()], table=tnode.name))
                        for p in lk_ports
                    ]
                    distinct = False
                src_id = make_id("op", "lookup-source", parent=lookup_id)
                ops, edges = self.sql_relation(
                    query,
                    tables,
                    outputs,
                    distinct,
                    src_id,
                    definition,
                    add_expr,
                    base_env,
                    dialect,
                    prefix=prefix,
                )
                extra_ops += ops
                extra_edges += edges
                extra_edges.append(
                    DataEdge(from_operation=src_id, to_operation=lookup_id, to_input="lookup")
                )
            else:
                if existing is not None and {p.name.upper() for p in lk_ports} <= {
                    c.name.upper() for c in self.datasets[existing[0]].columns
                }:
                    ds_id = existing[0]
                    mapping = [
                        ColumnMapping(from_column=c.name, to_column=lk_names[c.name.upper()].name)
                        for c in self.datasets[ds_id].columns
                        if c.name.upper() in lk_names
                    ]
                else:
                    ds_id = self.lookup_dataset(folder, definition, table, lk_ports, flat)
                    mapping = [
                        ColumnMapping(from_column=p.name, to_column=p.name) for p in lk_ports
                    ]
                extra_ops.append(
                    Operation(
                        id=read_id,
                        spec=ReadOp(dataset_id=ds_id),
                        source=self.ref(definition, "pc.lookup.read"),
                    )
                )
                extra_edges.append(
                    DataEdge(
                        from_operation=read_id,
                        to_operation=lookup_id,
                        to_input="lookup",
                        columns=mapping,
                    )
                )
        except SqlUnsupported as exc:
            raise Unsupported(f"lookup source SQL: {exc}") from exc
        kind, status, note = policy
        if override or sfilter:
            status = EvidenceStatus.APPROXIMATED
        return LookupParts(
            cond_text, cond, kind, status, note, in_ports, extra_ops, extra_edges, lk_ports
        )

    def build_lookup(
        self,
        folder: Folder,
        inst: RNode,
        definition: RNode,
        ports: list[Port],
        ins: list[Connector],
        session: RNode | None,
        oid: str,
        add_expr: Callable[..., str],
        env_for: Callable[..., Env],
        base_env: Env,
    ) -> Built:
        parts = self.lookup_parts(
            folder, inst.name, definition, ports, session, oid, add_expr, env_for, base_env, ""
        )
        self.require_connected(parts.in_ports, {c.to_field for c in ins})
        cond_id = add_expr("condition", parts.cond_text, parts.cond, definition)
        returns = [
            ColumnMapping(from_column=p.name, to_column=p.name)
            for p in ports
            if p.is_output and "LOOKUP" in p.porttype
        ]
        outs = [Column(name=p.name, type=p.type) for p in ports if p.is_output]
        return Built(
            LookupOp(
                condition_expression_id=cond_id,
                on_multiple_match=parts.kind,
                returns=returns,
            ),
            [OutputGroup(columns=outs)],
            "pc.op.lookup",
            parts.status,
            notes=[parts.note] if parts.note else [],
            extra_ops=parts.extra_ops,
            extra_edges=parts.extra_edges,
        )

    def lookup_dataset(
        self, folder: Folder, definition: RNode, table: str, lk_ports: list[Port], flat: bool
    ) -> str:
        """Dataset described by the lookup's own ports (no matching definition exists)."""
        key = (table, definition.name)
        ds_id = make_id("ds", folder.repository, folder.name, "lkp", *key)
        if ds_id not in self.datasets:
            src = self.ref(definition, "pc.dataset.lookup")
            bind_id = make_id("bind", folder.repository, folder.name, "lkp", *key)
            self.bindings[bind_id] = Binding(
                id=bind_id,
                name=table,
                resource_kind="path" if flat else "connection",
                reference="/".join((folder.name, "lookup", table)),
                source=src,
            )
            self.datasets[ds_id] = Dataset(
                id=ds_id,
                name=table,
                kind=DatasetKind.FILE if flat else DatasetKind.TABLE,
                columns=[Column(name=p.name, type=p.type) for p in lk_ports],
                binding_id=bind_id,
                format="delimited" if flat else None,
                source=src,
            )
            self.record(ds_id, EvidenceStatus.PRESERVED, src)
        return ds_id

    def mark_unsupported(self, flow: Dataflow, op_id: str, native: str, reason: str) -> Dataflow:
        ops = []
        for op in flow.operations:
            if op.id == op_id:
                op = op.model_copy(
                    update={"spec": UnsupportedOp(native_kind=native, reason=reason)}
                )
                self.evidence = [e for e in self.evidence if e.subject_id != op_id]
                self.record(op_id, EvidenceStatus.UNSUPPORTED, op.source)
            ops.append(op)
        edges, n = [], 0
        for e in flow.edges:
            if e.to_operation == op_id:
                n += 1
                e = e.model_copy(update={"to_input": f"in{n}"})
            edges.append(e)
        return flow.model_copy(update={"operations": ops, "edges": edges})

    def build_expression(
        self,
        ports: list[Port],
        connected: set[str],
        env_for: Callable[..., Env],
        add_expr: Callable[..., str],
        definition: RNode,
        calls: LookupCalls | None = None,
    ) -> Built:
        for p in ports:
            if p.is_input and p.default.strip():
                raise Unsupported(f"input port {p.name} replaces NULLs with a default value")
            if (
                p.is_output
                and not p.is_input
                and p.default.strip()
                and not _ERROR_DEFAULT.match(p.default)
            ):
                raise Unsupported(f"output port {p.name} has a custom error default value")
        inputs = [p for p in ports if p.is_input and p.name in connected]
        env: Env = env_for(inputs)
        env.lookup_call = calls
        for p in ports:
            if p.is_input and p.name not in connected:
                env.inline[p.name] = (LiteralNode(value=None, type=p.type), p.type)
        variables = [p for p in ports if p.is_variable]
        env.pending = {v.name for v in variables}
        for v in variables:
            # Still pending while its own expression is translated: a self-reference reads
            # the previous row's value (stateful).
            t = translate(v.expression, env)
            env.pending.discard(v.name)
            if t.opaque:
                reason = t.node.reason if isinstance(t.node, OpaqueNode) else ""
                env.inline[v.name] = (
                    OpaqueNode(text=v.expression, dialect=DIALECT, reason=reason),
                    v.type,
                )
                continue
            env.inline[v.name] = (convert(t, v.type, v.expression), v.type)
        assignments: list[Assignment] = []
        cols: list[Column] = []
        for p in ports:
            if not p.is_output:
                continue
            cols.append(Column(name=p.name, type=p.type))
            if p.is_input:
                if p.name not in connected:
                    null = Translation(LiteralNode(value=None, type=p.type), p.type, set())
                    assignments.append(
                        Assignment(
                            column=p.name, expression_id=add_expr(p.name, "NULL", null, p.node)
                        )
                    )
                continue
            if not p.expression.strip():
                t = Translation(
                    LiteralNode(value=None, type=p.type), p.type, {"pc.expr.empty-output"}
                )
            else:
                t = translate(p.expression, env)
            if not t.opaque:
                t = Translation(convert(t, p.type, p.expression), p.type, t.notes)
            assignments.append(
                Assignment(column=p.name, expression_id=add_expr(p.name, p.expression, t, p.node))
            )
        built = Built(
            DeriveOp(assignments=assignments), [OutputGroup(columns=cols)], "pc.op.expression"
        )
        if calls is not None and calls.calls:
            self.materialize_lookup_calls(built, calls, inputs, add_expr, definition)
        return built

    def materialize_lookup_calls(
        self,
        built: Built,
        calls: LookupCalls,
        inputs: list[Port],
        add_expr: Callable[..., str],
        anchor: RNode,
    ) -> None:
        """args derive -> lookup per call -> the expression (which reads ``__lkp<k>``)."""
        oid = calls.oid
        args_id = make_id("op", "lkp-args", parent=oid)
        cols = [Column(name=p.name, type=p.type) for p in inputs]
        assignments = []
        for call in calls.calls.values():
            for port, node in zip(call.parts.in_ports, call.args, strict=True):
                name = f"{call.column}.{port.name}"
                ex = add_expr(
                    f"lkp{call.k}.arg.{port.name}",
                    f":LKP.{call.name} argument",
                    Translation(node, port.type, set()),
                    anchor,
                )
                assignments.append(Assignment(column=name, expression_id=ex))
                cols.append(Column(name=name, type=port.type))
        built.extra_ops.append(
            Operation(
                id=args_id,
                spec=DeriveOp(assignments=assignments),
                outputs=[OutputGroup(columns=list(cols))],
                source=self.ref(anchor, "pc.lookup-call.args"),
            )
        )
        prev = args_id
        for call in calls.calls.values():
            lookup_id = make_id("op", f"lkp{call.k}", parent=oid)
            rename = {p.name: f"{call.column}.{p.name}" for p in call.parts.in_ports}
            # Lookup-side columns are namespaced per call so they cannot collide with the
            # calling transformation's own columns.
            side = {p.name: f"{call.column}#{p.name}" for p in call.parts.lk_ports}
            rename |= side
            cond = Translation(
                rename_columns(call.parts.cond.node, rename),
                call.parts.cond.type,
                call.parts.cond.notes,
            )
            cond_id = add_expr(
                f"lkp{call.k}.condition", call.parts.cond_text, cond, call.definition
            )
            cols = [*cols, Column(name=call.column, type=call.ret.type)]
            built.extra_ops.extend(call.parts.extra_ops)
            for edge in call.parts.extra_edges:
                if edge.to_operation == lookup_id and edge.to_input == "lookup":
                    maps = [(m.from_column, m.to_column) for m in edge.columns] or [
                        (n, n) for n in side
                    ]
                    edge = edge.model_copy(
                        update={
                            "columns": [
                                ColumnMapping(from_column=f, to_column=side[t]) for f, t in maps
                            ]
                        }
                    )
                built.extra_edges.append(edge)
            built.extra_ops.append(
                Operation(
                    id=lookup_id,
                    spec=LookupOp(
                        condition_expression_id=cond_id,
                        on_multiple_match=call.parts.kind,
                        returns=[
                            ColumnMapping(from_column=side[call.ret.name], to_column=call.column)
                        ],
                    ),
                    outputs=[OutputGroup(columns=list(cols))],
                    source=self.ref(call.definition, "pc.lookup-call"),
                )
            )
            built.extra_edges.append(DataEdge(from_operation=prev, to_operation=lookup_id))
            prev = lookup_id
            if call.parts.status is not EvidenceStatus.TRANSFORMED_EQUIVALENT:
                built.status = call.parts.status
        built.extra_edges.append(DataEdge(from_operation=prev, to_operation=oid))
        built.input_target = args_id

    def build_router(self, definition, ports, connected, env_for, add_expr) -> Built:  # type: ignore[no-untyped-def]
        inputs = [p for p in ports if p.group == "INPUT" or p.porttype == "INPUT"]
        self.require_connected(inputs, connected)
        in_cols = [Column(name=p.name, type=p.type) for p in inputs]
        groups, default, outputs = [], None, []
        for g in sorted(definition.children("GROUP"), key=lambda n: int(n.get("ORDER") or 0)):
            gtype = g.get("TYPE")
            if gtype == "INPUT":
                continue
            outputs.append(OutputGroup(name=g.name, columns=in_cols))
            if gtype == "OUTPUT/DEFAULT":
                default = g.name
                continue
            text = g.get("EXPRESSION") or "FALSE"
            t = as_condition(translate(text, env_for(inputs)), text)
            groups.append(
                RouteGroup(
                    name=g.name, predicate_expression_id=add_expr(f"group.{g.name}", text, t, g)
                )
            )
        for p in ports:
            if p.is_output and p.ref_field not in {c.name for c in in_cols}:
                raise Unsupported(f"router output port {p.name} has no input reference")
        return Built(RouteOp(groups=groups, default_group=default), outputs, "pc.op.router")

    def build_joiner(self, definition, ports, connected, env_for, add_expr) -> Built:  # type: ignore[no-untyped-def]
        attrs = definition.attributes("TABLEATTRIBUTE")
        jt = {
            "normal join": "inner",
            "master outer join": "left",
            "detail outer join": "right",
            "full outer join": "full",
        }.get(attrs.get("Join Type", "Normal Join").lower())
        if jt is None:
            raise Unsupported(f"join type '{attrs.get('Join Type')}'")
        if attrs.get("Case Sensitive String Comparison", "YES") == "NO":
            raise Unsupported("case-insensitive join comparison")
        inputs = [p for p in ports if p.is_input]
        self.require_connected(inputs, connected)
        if not any(p.is_master for p in inputs) or all(p.is_master for p in inputs):
            raise Unsupported("joiner needs both master and detail ports")
        text = attrs.get("Join Condition", "")
        if not text.strip():
            raise Unsupported("joiner without a join condition")
        t = as_condition(translate(text, env_for(inputs)), text)
        cols = [Column(name=p.name, type=p.type) for p in ports if p.is_output]
        return Built(
            JoinOp(
                join_type=jt,
                condition_expression_id=add_expr("condition", text, t, definition),
            ),
            [OutputGroup(columns=cols)],
            "pc.op.joiner",
        )

    def build_aggregator(self, ports, connected, env_for, add_expr, definition, oid) -> Built:  # type: ignore[no-untyped-def]
        keys = [p for p in ports if p.exprtype == "GROUPBY"]
        for p in ports:
            if p.is_variable:
                raise Unsupported(f"variable port {p.name} in aggregator")
            if p.is_input and p.is_output and p.exprtype != "GROUPBY":
                raise Unsupported(f"port {p.name} passes the last row's value (order dependent)")
        inputs = [p for p in ports if p.is_input]
        self.require_connected(inputs, connected)
        env = env_for(inputs, allow_aggregates=True, group_keys={k.name for k in keys})
        aggs: list[Aggregation] = []
        cols: list[Column] = []
        for p in ports:
            if not p.is_output:
                continue
            cols.append(Column(name=p.name, type=p.type))
            if p.exprtype == "GROUPBY":
                continue
            t = translate(p.expression, env)
            if not t.opaque:
                t = Translation(convert(t, p.type, p.expression), p.type, t.notes)
            aggs.append(
                Aggregation(column=p.name, expression_id=add_expr(p.name, p.expression, t, p.node))
            )
        if keys:
            return Built(
                AggregateOp(group_by=[k.name for k in keys], aggregations=aggs),
                [OutputGroup(columns=cols)],
                "pc.op.aggregator",
            )
        return self.global_aggregator(aggs, cols, add_expr, definition, oid)

    def global_aggregator(self, aggs, cols, add_expr, definition, oid) -> Built:  # type: ignore[no-untyped-def]
        """An Aggregator without group-by ports emits one row, or none for empty input
        (a canonical global aggregate always emits one): aggregate with a row count, keep
        the row only when the count is positive, then drop the count."""
        rows = "__rows"
        count_t = DataType(kind=TypeKind.BIGINT)
        count_id = add_expr(
            "rows",
            "COUNT(*)",
            Translation(CallNode(function="count_all", args=[]), count_t, set()),
            definition,
        )
        nonempty = CallNode(
            function="gt",
            args=[ColumnRefNode(name=rows), LiteralNode(value=0, type=count_t)],
        )
        cond_id = add_expr(
            "nonempty",
            "COUNT(*) > 0",
            Translation(nonempty, DataType(kind=TypeKind.BOOLEAN), set()),
            definition,
        )
        agg_id = make_id("op", "aggregate", parent=oid)
        filter_id = make_id("op", "nonempty", parent=oid)
        with_rows = [*cols, Column(name=rows, type=count_t)]
        src = self.ref(definition, "pc.op.aggregator-global")
        built = Built(
            ProjectOp(columns=[c.name for c in cols]),
            [OutputGroup(columns=cols)],
            "pc.op.aggregator-global",
            EvidenceStatus.APPROXIMATED,
            [
                "PowerCenter emits no row for empty input (documented community behavior, "
                "not verified against a live runtime)"
            ],
        )
        built.extra_ops = [
            Operation(
                id=agg_id,
                spec=AggregateOp(
                    group_by=[],
                    aggregations=[*aggs, Aggregation(column=rows, expression_id=count_id)],
                ),
                outputs=[OutputGroup(columns=with_rows)],
                source=src,
            ),
            Operation(
                id=filter_id,
                spec=FilterOp(predicate_expression_id=cond_id),
                outputs=[OutputGroup(columns=with_rows)],
                source=src,
            ),
        ]
        built.extra_edges = [
            DataEdge(from_operation=agg_id, to_operation=filter_id),
            DataEdge(from_operation=filter_id, to_operation=oid),
        ]
        built.input_target = agg_id
        return built

    # ------------------------------------------------------------------ pipeline

    def pipeline(
        self, folder: Folder, wf: RNode, session_df: dict[tuple[str, ...], str | None]
    ) -> None:
        pid = make_id("pl", folder.repository, folder.name, wf.name)
        psrc = self.ref(wf, "pc.pipeline.workflow")
        local = {c.name: c for c in wf.children() if c.tag in ("SESSION", "TASK", "WORKLET")}
        instances = [ti for ti in wf.children("TASKINSTANCE")]
        start = {ti.name for ti in instances if ti.get("TASKTYPE") == "Start"}
        names = {ti.name for ti in instances}
        tid = {n: make_id("task", folder.repository, folder.name, wf.name, n) for n in names}
        links: dict[str, list[RNode]] = {}
        for link in wf.children("WORKFLOWLINK"):
            frm, to = link.get("FROMTASK"), link.get("TOTASK")
            if frm not in names or to not in names:
                self.diag(
                    "PC-R-003",
                    Severity.ERROR,
                    f"Link {frm} -> {to} has an unknown end.",
                    pid,
                    self.ref(link, "pc.link"),
                )
                continue
            links.setdefault(to, []).append(link)
        sched = wf.child("SCHEDULER")
        info = sched.child("SCHEDULEINFO") if sched is not None else None
        if info is not None and info.get("SCHEDULETYPE") not in ("", "ONDEMAND"):
            self.diag(
                "PC-N-004",
                Severity.INFO,
                f"Schedule type {info.get('SCHEDULETYPE')} is not translated.",
                pid,
                self.ref(info, "pc.pipeline.schedule"),
            )

        tasks: list[Task] = []
        pexprs: list[Expression] = []
        for ti in instances:
            if ti.name in start:
                continue
            task_id = tid[ti.name]
            src = self.ref(ti, "pc.task")
            kind, df_id, reason, status = self.task_kind(folder, wf, ti, local, session_df)
            deps: list[Dependency] = []
            for link in links.get(ti.name, []):
                frm = link.get("FROMTASK")
                if frm in start:
                    continue
                cond_text = link.get("CONDITION")
                m = _LINK.match(cond_text)
                if not cond_text.strip():
                    deps.append(
                        Dependency(task_id=tid[frm], condition=DependencyCondition.COMPLETION)
                    )
                elif m and m.group(1).lower() == frm.lower():
                    cond = (
                        DependencyCondition.SUCCESS
                        if m.group(2).upper() == "SUCCEEDED"
                        else DependencyCondition.FAILURE
                    )
                    deps.append(Dependency(task_id=tid[frm], condition=cond))
                else:
                    ex_id = make_id("ex", "link", frm, parent=task_id)
                    pexprs.append(
                        Expression(
                            id=ex_id,
                            ast=OpaqueNode(
                                text=cond_text,
                                dialect=DIALECT,
                                reason="link condition outside the supported forms",
                            ),
                            original_text=cond_text,
                            original_dialect=DIALECT,
                            source=self.ref(link, "pc.link.condition"),
                        )
                    )
                    deps.append(
                        Dependency(
                            task_id=tid[frm],
                            condition=DependencyCondition.EXPRESSION,
                            expression_id=ex_id,
                        )
                    )
            tasks.append(
                Task(
                    id=task_id,
                    name=ti.name,
                    kind=kind,
                    dataflow_id=df_id,
                    depends_on=deps,
                    trigger="any" if ti.get("TREAT_INPUTLINK_AS_AND") == "NO" else "all",
                    unsupported_reason=reason,
                    source=src,
                )
            )
            self.record(task_id, status, src)
        self.pipelines.append(
            Pipeline(id=pid, name=wf.name, tasks=tasks, expressions=pexprs, source=psrc)
        )
        self.record(pid, EvidenceStatus.PRESERVED, psrc)

    def task_kind(
        self,
        folder: Folder,
        wf: RNode,
        ti: RNode,
        local: dict[str, RNode],
        session_df: dict[tuple[str, ...], str | None],
    ) -> tuple[TaskKind, str | None, str | None, EvidenceStatus]:
        ttype, tname = ti.get("TASKTYPE"), ti.get("TASKNAME")
        if ti.get("ISENABLED", "YES") == "NO":
            return TaskKind.UNSUPPORTED, None, "disabled task", EvidenceStatus.UNSUPPORTED
        if ttype == "Session":
            key: tuple[str, ...] | None = None
            if tname in local and local[tname].tag == "SESSION":
                key = (wf.name, tname)
            elif folder.get("SESSION", tname) is not None:
                key = (tname,)
            if key is None:
                self.diag(
                    "PC-R-001",
                    Severity.ERROR,
                    f"Session '{tname}' not found.",
                    None,
                    self.ref(ti, "pc.task"),
                )
                return (
                    TaskKind.UNSUPPORTED,
                    None,
                    f"session '{tname}' not found",
                    EvidenceStatus.MISSING_INFORMATION,
                )
            df_id = session_df.get(key)
            if df_id is None:
                return (
                    TaskKind.UNSUPPORTED,
                    None,
                    f"mapping of session '{tname}' not found",
                    EvidenceStatus.MISSING_INFORMATION,
                )
            return TaskKind.DATAFLOW, df_id, None, EvidenceStatus.PRESERVED
        if ttype == "Worklet":
            return (
                TaskKind.UNSUPPORTED,
                None,
                "worklets are not expanded",
                EvidenceStatus.UNSUPPORTED,
            )
        kind = _TASK_KINDS.get(ttype)
        if kind is None:
            return TaskKind.UNSUPPORTED, None, f"task type '{ttype}'", EvidenceStatus.UNSUPPORTED
        return kind, None, None, EvidenceStatus.PRESERVED


# ---------------------------------------------------------------------- conversions


def as_condition(t: Translation, text: str) -> Translation:
    if t.opaque or t.type.kind in (TypeKind.BOOLEAN, TypeKind.UNKNOWN):
        return t
    if t.type.kind in NUMERIC:
        zero = LiteralNode(value=0, type=DataType(kind=TypeKind.INTEGER))
        return Translation(
            CallNode(function="ne", args=[t.node, zero]), DataType(kind=TypeKind.BOOLEAN), t.notes
        )
    return Translation(
        OpaqueNode(
            text=text, dialect=DIALECT, reason=f"{t.type.kind.value} value used as a condition"
        ),
        DataType(kind=TypeKind.UNKNOWN),
        set(),
    )


def convert(t: Translation, target: DataType, text: str) -> ExpressionNode:
    """Node converting ``t`` to ``target`` (explicit cast when types differ)."""
    src = t.type.kind
    if src is TypeKind.UNKNOWN or (src is target.kind and src is not TypeKind.DECIMAL):
        return t.node
    if src is TypeKind.DECIMAL and target.kind is TypeKind.DECIMAL and t.type == target:
        return t.node
    numeric_like = set(NUMERIC) | {TypeKind.BOOLEAN}
    if src in numeric_like and target.kind in NUMERIC:
        return CastNode(to=target, arg=t.node)
    if src is target.kind:
        return t.node
    whole = src in (TypeKind.INTEGER, TypeKind.BIGINT) or (
        src is TypeKind.DECIMAL and t.type.precision is not None and not t.type.scale
    )
    if whole and target.kind is TypeKind.STRING:  # exact: the number's decimal digits
        return CallNode(function="to_string", args=[t.node])
    if src is TypeKind.BOOLEAN and target.kind is TypeKind.STRING:  # TRUE/FALSE are 1/0
        integer = CastNode(to=DataType(kind=TypeKind.INTEGER), arg=t.node)
        return CallNode(function="to_string", args=[integer])
    return OpaqueNode(
        text=text,
        dialect=DIALECT,
        reason=f"implicit conversion from {src.value} to {target.kind.value}",
    )


__all__ = ["ADAPTER_ID", "APPROXIMATION_NOTES", "CODES", "Normalizer"]
