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

from enum import StrEnum
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


class TypeKind(StrEnum):
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
        default=None,
        description="Literal value (absent or null for NULL). Decimals and timestamps are "
        "encoded as strings.",
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


class CastNode(_Model):
    """Explicit conversion to ``to``; the conversion rules are in docs/semantics.md."""

    node: Literal["cast"] = "cast"
    to: DataType
    arg: ExpressionNode


class OpaqueNode(_Model):
    """Expression text that was not parsed into the supported grammar."""

    node: Literal["opaque"] = "opaque"
    text: str
    dialect: str
    reason: str | None = None


ExpressionNode = Annotated[
    Union[LiteralNode, ColumnRefNode, ParameterRefNode, CallNode, CastNode, OpaqueNode],  # noqa: UP007
    Field(discriminator="node"),
]
CallNode.model_rebuild()
CastNode.model_rebuild()


class Expression(_Model):
    id: Identifier
    ast: ExpressionNode
    original_text: str | None = None
    original_dialect: str | None = None
    result_type: DataType | None = None
    source: SourceRef


# ---------------------------------------------------------------------------- operations


class ColumnMapping(_Model):
    from_column: str
    to_column: str


class WriteMode(StrEnum):
    APPEND = "append"
    OVERWRITE = "overwrite"
    ERROR_IF_EXISTS = "error_if_exists"
    UPDATE = "update"
    UPSERT = "upsert"


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
    """Rows of ``dataset_id``; output columns are the dataset's columns."""

    kind: Literal["read"] = "read"
    dataset_id: Identifier


class WriteOp(_Model):
    """Write the input to ``dataset_id``. Dataset columns without an input column are NULL.

    Keyed modes match input rows to existing rows on ``keys`` (plain equality, so NULL keys
    never match): ``update`` sets the input's columns on matching rows and discards input
    rows without a match; ``upsert`` also inserts them. Columns the input does not provide
    keep their existing values. Duplicate keys in the input fail the task.
    """

    kind: Literal["write"] = "write"
    dataset_id: Identifier
    mode: WriteMode = WriteMode.APPEND
    keys: list[str] = Field(default_factory=list, description="Key columns of keyed modes.")


class ProjectOp(_Model):
    """Keep ``columns`` of the input, cast to the declared output column types."""

    kind: Literal["project"] = "project"
    columns: list[str]


class DeriveOp(_Model):
    """Per row, compute each assignment from the input row (all see the same input row).

    The output relation holds the declared output columns: assigned columns take the
    assignment value; unassigned output columns pass through from the input. All values
    are cast to the declared output column types.
    """

    kind: Literal["derive"] = "derive"
    assignments: list[Assignment]


class FilterOp(_Model):
    """Keep rows whose predicate is TRUE (NULL counts as not TRUE)."""

    kind: Literal["filter"] = "filter"
    predicate_expression_id: Identifier


class RouteOp(_Model):
    """Route rows to every group whose predicate is TRUE (a row may reach several groups).

    Rows for which no predicate is TRUE go to ``default_group`` if one is declared, and are
    dropped otherwise. Each group's output relation has the input columns.
    """

    kind: Literal["route"] = "route"
    groups: list[RouteGroup]
    default_group: str | None = None


class JoinOp(_Model):
    """Join input slots ``left`` and ``right`` on a boolean condition.

    Column names of the two slots must be disjoint. NULL keys never match. ``left``
    keeps all left rows, ``right`` all right rows, ``full`` both.
    """

    kind: Literal["join"] = "join"
    join_type: Literal["inner", "left", "right", "full"]
    condition_expression_id: Identifier


class LookupOp(_Model):
    """For each row of slot ``in``, find the rows of slot ``lookup`` where the condition is
    TRUE (NULL never matches) and return the mapped lookup columns.

    Input columns pass through by name; ``returns`` maps lookup columns to output columns,
    which are NULL when nothing matches. Column names of the two slots must be disjoint.
    ``on_multiple_match``:

    * ``any``: one matching row: the first when the lookup slot's columns are sorted
      ascending in declared order with NULLs last (deterministic);
    * ``error``: the task fails if any input row has more than one match;
    * ``all``: one output row per match (not row-preserving).
    """

    kind: Literal["lookup"] = "lookup"
    condition_expression_id: Identifier
    on_multiple_match: Literal["any", "error", "all"]
    returns: list[ColumnMapping]


class AggregateOp(_Model):
    """Group by ``group_by`` (NULL keys form one group) and compute ``aggregations``.

    Aggregation expressions may combine aggregate functions and group keys only. Output
    columns are the group keys followed by the aggregation columns, cast to declared types.
    """

    kind: Literal["aggregate"] = "aggregate"
    group_by: list[str]
    aggregations: list[Aggregation]


class SequenceOp(_Model):
    """Pass the input through and add ``column`` (bigint): the input rows, ordered by all
    their columns ascending with NULLs first, receive start, start + increment, … where
    start is the value of parameter ``start_parameter_id``. Rows that are equal in every
    column are interchangeable, so the result is deterministic. When slot ``after`` is
    connected, numbering continues after as many values as ``after`` has rows (start +
    count(after) * increment): two consumers of one generator get consecutive blocks."""

    kind: Literal["sequence"] = "sequence"
    column: str
    start_parameter_id: Identifier
    increment: int = 1


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
        SequenceOp,
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


class DataEdge(_Model):
    """Rows flow from an output group to an input slot.

    ``columns`` renames upstream columns into the downstream operation's input columns.
    An empty list passes every upstream column under its own name. All edges into one slot
    must come from the same upstream operation and group.
    """

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


class TaskKind(StrEnum):
    DATAFLOW = "dataflow"
    COMMAND = "command"
    WAIT = "wait"
    NOTIFY = "notify"
    ASSIGNMENT = "assignment"
    DECISION = "decision"
    UNSUPPORTED = "unsupported"


class DependencyCondition(StrEnum):
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
    trigger: Literal["all", "any"] = Field(
        default="all", description="Run when all (or any) dependencies are satisfied."
    )
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


class DatasetKind(StrEnum):
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


class ParameterScope(StrEnum):
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
    builtin: Literal["run_start_time"] | None = Field(
        default=None,
        description="Value supplied by the runtime when the run does not set it: "
        "run_start_time is the run's start instant (UTC), identical for every task of a run.",
    )
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
