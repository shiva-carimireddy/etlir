"""Canonical IR data model.

The Canonical IR is source- and target-independent. Rules enforced by tests:

* No source-product or target-product names appear in this schema.
* Every entity carries a :class:`SourceRef` so it can be traced to the source artifact
  and the normalization rule that produced it.
* The workflow (task dependency) graph and the dataflow (operation) graph are distinct.
* Unsupported constructs are represented explicitly, never dropped.
* Bindings name where a runtime resource comes from; they never hold secret values.

Structural invariants that JSON Schema cannot express (reference resolution, acyclicity,
unique identifiers) are checked by :mod:`etlir.canonical.invariants`.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Final, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

IR_VERSION: Final = "0.1.0"

Identifier = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_.:\-]{0,254}$",
        description="Stable identifier, unique within a CanonicalDocument.",
    ),
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------- provenance


class SourceRef(_Model):
    """Link from a canonical entity back to its source artifact (source trace)."""

    adapter: str = Field(description="Registered source adapter id that produced the entity.")
    artifact: str = Field(description="Input artifact identity, e.g. a relative path.")
    locator: str = Field(description="Location inside the artifact, e.g. an XPath.")
    native_id: str | None = Field(default=None, description="Identifier in the source system.")
    rule: str | None = Field(default=None, description="Normalization rule id applied.")


# ---------------------------------------------------------------------------- data types


class TypeKind(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    BIGINT = "bigint"
    DECIMAL = "decimal"
    DOUBLE = "double"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"
    BINARY = "binary"
    UNKNOWN = "unknown"


class DataType(_Model):
    kind: TypeKind
    length: int | None = Field(default=None, ge=0)
    precision: int | None = Field(default=None, ge=1)
    scale: int | None = Field(default=None, ge=0)
    nullable: bool = True


class Column(_Model):
    name: str
    type: DataType


# --------------------------------------------------------------------------- expressions


class LiteralNode(_Model):
    node: Literal["literal"] = "literal"
    value: str | int | bool | None = Field(
        description="Literal value. Decimals and timestamps are encoded as strings."
    )
    type: DataType


class ColumnRefNode(_Model):
    node: Literal["column"] = "column"
    name: str


class ParameterRefNode(_Model):
    node: Literal["parameter"] = "parameter"
    parameter_id: Identifier


class CallNode(_Model):
    node: Literal["call"] = "call"
    function: str = Field(description="Name in the canonical function catalog.")
    args: list[ExpressionNode] = Field(default_factory=list)


class OpaqueNode(_Model):
    """Expression text that was not parsed into the supported grammar."""

    node: Literal["opaque"] = "opaque"
    text: str
    dialect: str


ExpressionNode = Annotated[
    Union[LiteralNode, ColumnRefNode, ParameterRefNode, CallNode, OpaqueNode],  # noqa: UP007
    Field(discriminator="node"),
]
CallNode.model_rebuild()


class Expression(_Model):
    id: Identifier
    ast: ExpressionNode
    original_text: str | None = None
    original_dialect: str | None = None
    result_type: DataType | None = None
    source: SourceRef


# ---------------------------------------------------------------------------- operations


class WriteMode(str, Enum):
    APPEND = "append"
    OVERWRITE = "overwrite"
    ERROR_IF_EXISTS = "error_if_exists"


class Assignment(_Model):
    column: str
    expression_id: Identifier


class RouteGroup(_Model):
    name: str
    predicate_expression_id: Identifier


class Aggregation(_Model):
    column: str
    expression_id: Identifier


class ReadOp(_Model):
    kind: Literal["read"] = "read"
    dataset_id: Identifier


class WriteOp(_Model):
    kind: Literal["write"] = "write"
    dataset_id: Identifier
    mode: WriteMode = WriteMode.APPEND


class ProjectOp(_Model):
    kind: Literal["project"] = "project"
    columns: list[str]


class DeriveOp(_Model):
    kind: Literal["derive"] = "derive"
    assignments: list[Assignment]


class FilterOp(_Model):
    kind: Literal["filter"] = "filter"
    predicate_expression_id: Identifier


class RouteOp(_Model):
    """Evaluate each group predicate per row; rows matching no group go to ``default_group``."""

    kind: Literal["route"] = "route"
    groups: list[RouteGroup]
    default_group: str | None = None


class JoinOp(_Model):
    kind: Literal["join"] = "join"
    join_type: Literal["inner", "left", "right", "full"]
    condition_expression_id: Identifier


class LookupOp(_Model):
    kind: Literal["lookup"] = "lookup"
    dataset_id: Identifier
    condition_expression_id: Identifier
    on_multiple_match: Literal["error", "first", "last", "any", "all"]
    return_columns: list[str]


class AggregateOp(_Model):
    kind: Literal["aggregate"] = "aggregate"
    group_by: list[str]
    aggregations: list[Aggregation]


class UnionOp(_Model):
    kind: Literal["union"] = "union"
    distinct: bool = False


class UnsupportedOp(_Model):
    """A recognized source construct with no canonical semantics yet. Always blocks emission."""

    kind: Literal["unsupported"] = "unsupported"
    native_kind: str
    reason: str


OperationSpec = Annotated[
    Union[  # noqa: UP007
        ReadOp,
        WriteOp,
        ProjectOp,
        DeriveOp,
        FilterOp,
        RouteOp,
        JoinOp,
        LookupOp,
        AggregateOp,
        UnionOp,
        UnsupportedOp,
    ],
    Field(discriminator="kind"),
]


class OutputGroup(_Model):
    """Named output relation of an operation (most operations have exactly one)."""

    name: str = "out"
    columns: list[Column]


class Operation(_Model):
    id: Identifier
    spec: OperationSpec
    outputs: list[OutputGroup] = Field(default_factory=list)
    source: SourceRef


class ColumnMapping(_Model):
    from_column: str
    to_column: str


class DataEdge(_Model):
    from_operation: Identifier
    from_group: str = "out"
    to_operation: Identifier
    to_input: str = Field(default="in", description="Input slot, e.g. 'left'/'right' for joins.")
    columns: list[ColumnMapping] = Field(default_factory=list)


class Dataflow(_Model):
    """Operation graph for one data-movement unit. Distinct from the task graph."""

    id: Identifier
    name: str
    operations: list[Operation]
    edges: list[DataEdge]
    expressions: list[Expression] = Field(default_factory=list)
    source: SourceRef


# --------------------------------------------------------------------- workflow (control)


class TaskKind(str, Enum):
    DATAFLOW = "dataflow"
    COMMAND = "command"
    WAIT = "wait"
    NOTIFY = "notify"
    ASSIGNMENT = "assignment"
    DECISION = "decision"
    UNSUPPORTED = "unsupported"


class DependencyCondition(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    COMPLETION = "completion"
    EXPRESSION = "expression"


class Dependency(_Model):
    task_id: Identifier
    condition: DependencyCondition = DependencyCondition.SUCCESS
    expression_id: Identifier | None = Field(
        default=None, description="Required when condition is 'expression'."
    )


class Task(_Model):
    id: Identifier
    name: str
    kind: TaskKind
    dataflow_id: Identifier | None = Field(default=None, description="Required for dataflow tasks.")
    depends_on: list[Dependency] = Field(default_factory=list)
    parameter_ids: list[Identifier] = Field(default_factory=list)
    unsupported_reason: str | None = None
    source: SourceRef


class Pipeline(_Model):
    """Logical unit of orchestration: a task dependency graph."""

    id: Identifier
    name: str
    tasks: list[Task]
    expressions: list[Expression] = Field(
        default_factory=list, description="Expressions used by task-level conditions."
    )
    source: SourceRef


# ------------------------------------------------------------------ datasets & runtime


class DatasetKind(str, Enum):
    TABLE = "table"
    FILE = "file"
    QUEUE = "queue"
    API = "api"
    OTHER = "other"


class Dataset(_Model):
    id: Identifier
    name: str
    kind: DatasetKind
    columns: list[Column] = Field(default_factory=list)
    binding_id: Identifier | None = None
    format: str | None = Field(default=None, description="Physical format, e.g. csv, parquet.")
    source: SourceRef


class ParameterScope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"
    PIPELINE = "pipeline"
    TASK = "task"
    DATAFLOW = "dataflow"
    ENVIRONMENT = "environment"


class Parameter(_Model):
    id: Identifier
    name: str
    scope: ParameterScope
    type: DataType
    default: str | None = Field(default=None, description="Never populated for sensitive values.")
    sensitive: bool = False
    source: SourceRef


class Binding(_Model):
    """Maps a logical resource to a runtime-resolved reference. Holds no secret values."""

    id: Identifier
    name: str
    resource_kind: Literal["connection", "path", "credential", "compute", "other"]
    reference: str = Field(description="Name resolved at runtime, e.g. an environment variable.")
    source: SourceRef


class LineageEdge(_Model):
    """Column-level data lineage (distinct from source provenance)."""

    target: str = Field(description="dataset_id.column")
    sources: list[str] = Field(description="dataset_id.column entries")
    dataflow_id: Identifier
    operation_ids: list[Identifier]
    derivation: Literal["direct", "expression", "aggregate", "lookup", "unknown"]
    source: SourceRef


# ------------------------------------------------------------------------------ document


class CanonicalDocument(_Model):
    ir_version: Literal["0.1.0"] = IR_VERSION
    pipelines: list[Pipeline] = Field(default_factory=list)
    dataflows: list[Dataflow] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    parameters: list[Parameter] = Field(default_factory=list)
    bindings: list[Binding] = Field(default_factory=list)
    lineage: list[LineageEdge] = Field(default_factory=list)
