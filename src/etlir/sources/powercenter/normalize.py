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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from etlir.canonical.functions import NUMERIC
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
    OpaqueNode,
    Operation,
    OperationSpec,
    OutputGroup,
    Parameter,
    ParameterScope,
    Pipeline,
    ProjectOp,
    ReadOp,
    RouteGroup,
    RouteOp,
    SourceRef,
    Task,
    TaskKind,
    TypeKind,
    UnsupportedOp,
    WriteMode,
    WriteOp,
)
from etlir.evidence import Diagnostic, EvidenceRecord, EvidenceStatus, Severity
from etlir.sources.powercenter.expression import (
    APPROXIMATION_NOTES,
    DIALECT,
    Env,
    Translation,
    translate,
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
        return CanonicalDocument(
            pipelines=self.pipelines,
            dataflows=[self.dataflows[k] for k in sorted(self.dataflows)],
            datasets=[self.datasets[k] for k in sorted(self.datasets)],
            parameters=[self.parameters[k] for k in sorted(self.parameters)],
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

    def mapping_env(self, folder: Folder, mapping: RNode) -> Env:
        env = Env()
        for v in mapping.children("MAPPINGVARIABLE"):
            key = v.name.lstrip("$").upper()
            if v.get("ISPARAM") != "YES":
                env.stateful.add(key)
                continue
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
                    source=self.ref(v, "pc.parameter.mapping"),
                ),
            )
            env.parameters[key] = (pid, ptype)
        return env

    # ------------------------------------------------------------------ dataflow

    def dataflow(
        self, folder: Folder, mapping: RNode, session: RNode | None, df_id: str, name: str
    ) -> None:
        df_src = self.ref(
            session or mapping, "pc.dataflow.session" if session else "pc.dataflow.mapping"
        )
        base_env = self.mapping_env(folder, mapping)
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
        incoming: dict[tuple[str, str], list[Connector]] = {}
        outgoing: dict[tuple[str, str], list[Connector]] = {}
        for c in connectors:
            incoming.setdefault(c.to_inst, []).append(c)
            outgoing.setdefault(c.from_inst, []).append(c)

        definitions: dict[tuple[str, str], RNode | None] = {}
        for key, inst in instances.items():
            definitions[key] = self.resolve_instance(folder, inst, local)
        name_count: dict[str, int] = {}
        for name, _ in instances:
            name_count[name] = name_count.get(name, 0) + 1

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
        uses_decimal = False
        for inst_key, inst in instances.items():
            oid = op_id(inst_key)
            definition = definitions[inst_key]
            ttype = inst.get("TRANSFORMATION_TYPE")
            ins = incoming.get(inst_key, [])
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
            operations.append(Operation(id=oid, spec=built.spec, outputs=built.outputs, source=src))
            self.record(oid, built.status, src)
            uses_decimal = uses_decimal or any(
                c.type.kind is TypeKind.DECIMAL for g in built.outputs for c in g.columns
            )

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
                        to_operation=oid,
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
        self.dataflows[df_id] = Dataflow(
            id=df_id,
            name=name,
            operations=operations,
            edges=edges,
            expressions=expressions,
            source=df_src,
        )
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
    ) -> Built:
        if ttype != "Joiner" and len(ups) > 1:
            raise Unsupported(
                "inputs from several upstream transformations (row-aligned merge) are not supported"
            )
        if ttype == "Source Definition":
            return self.build_read(folder, definition)
        if ttype == "Target Definition":
            return self.build_write(folder, inst, definition, session, oid)
        if definition.tag == "MAPPLET":
            raise Unsupported("mapplet expansion is not supported")

        ports = _ports(definition)
        connected = {c.to_field for c in ins}

        def add_expr(suffix: str, text: str, t: Translation, anchor: RNode) -> str:
            ex_id = make_id("ex", suffix, parent=oid)
            rule = "pc.expr.opaque" if t.opaque else "pc.expr"
            self.expr_notes[ex_id] = set(t.notes)
            expressions.append(
                Expression(
                    id=ex_id,
                    ast=t.node,
                    original_text=text,
                    original_dialect=DIALECT,
                    result_type=t.type,
                    source=self.ref(anchor, rule),
                )
            )
            return ex_id

        def env_for(inputs: list[Port], **kw: object) -> Env:
            env = Env(parameters=dict(base_env.parameters), stateful=set(base_env.stateful))
            for k, v in kw.items():
                setattr(env, k, v)
            for p in inputs:
                env.columns[p.name] = p.type
            return env

        if ttype == "Source Qualifier":
            return self.build_sq(inst, definition, ports, connected, session)
        if ttype == "Expression":
            return self.build_expression(ports, connected, env_for, add_expr, definition)
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
            return self.build_aggregator(ports, connected, env_for, add_expr, definition)
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
        self, folder: Folder, inst: RNode, definition: RNode, session: RNode | None, oid: str
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
        treat = session.attributes().get("Treat source rows as", "Insert")
        if treat.lower() != "insert":
            raise Unsupported(f"session treats source rows as '{treat}' (update strategy)")
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

    def build_sq(
        self,
        inst: RNode,
        definition: RNode,
        ports: list[Port],
        connected: set[str],
        session: RNode | None,
    ) -> Built:
        attrs = definition.attributes("TABLEATTRIBUTE")
        found = [k for k in _SQ_OVERRIDES if attrs.get(k, "").strip()]
        if session is not None:
            for node in [
                *session.children("SESSTRANSFORMATIONINST"),
                *session.children("SESSIONEXTENSION"),
            ]:
                if node.get("SINSTANCENAME") == inst.name:
                    a = node.attributes()
                    found += [k for k in _SQ_OVERRIDES if a.get(k, "").strip()]
        if attrs.get("Select Distinct", "NO") == "YES":
            found.append("Select Distinct")
        if found:
            raise Unsupported(
                f"source qualifier uses {sorted(set(found))} (SQL overrides are "
                "not in the accepted subset)"
            )
        self.require_connected(ports, connected)
        cols = [Column(name=p.name, type=p.type) for p in ports if p.is_output]
        return Built(
            ProjectOp(columns=[c.name for c in cols]),
            [OutputGroup(columns=cols)],
            "pc.op.source-qualifier",
            EvidenceStatus.PRESERVED,
        )

    def build_expression(self, ports, connected, env_for, add_expr, definition) -> Built:  # type: ignore[no-untyped-def]
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
        for p in ports:
            if p.is_input and p.name not in connected:
                env.inline[p.name] = (LiteralNode(value=None, type=p.type), p.type)
        variables = [p for p in ports if p.is_variable]
        env.pending = {v.name for v in variables}
        for v in variables:
            env.pending.discard(v.name)
            t = translate(v.expression, env)
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
            t = translate(p.expression, env)
            if not t.opaque:
                t = Translation(convert(t, p.type, p.expression), p.type, t.notes)
            assignments.append(
                Assignment(column=p.name, expression_id=add_expr(p.name, p.expression, t, p.node))
            )
        return Built(
            DeriveOp(assignments=assignments), [OutputGroup(columns=cols)], "pc.op.expression"
        )

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

    def build_aggregator(self, ports, connected, env_for, add_expr, definition) -> Built:  # type: ignore[no-untyped-def]
        keys = [p for p in ports if p.exprtype == "GROUPBY"]
        if not keys:
            raise Unsupported("aggregator without group-by ports (empty-input behavior unverified)")
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
        return Built(
            AggregateOp(group_by=[k.name for k in keys], aggregations=aggs),
            [OutputGroup(columns=cols)],
            "pc.op.aggregator",
        )

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
    return OpaqueNode(
        text=text,
        dialect=DIALECT,
        reason=f"implicit conversion from {src.value} to {target.kind.value}",
    )


__all__ = ["ADAPTER_ID", "APPROXIMATION_NOTES", "CODES", "Normalizer"]
