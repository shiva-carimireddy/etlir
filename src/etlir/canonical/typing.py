"""Type inference over canonical expressions (used by emitters to lower casts exactly)."""

from __future__ import annotations

from collections.abc import Mapping

from etlir.canonical.functions import CATALOG, result_type
from etlir.canonical.model import (
    CallNode,
    CastNode,
    ColumnRefNode,
    DataType,
    ExpressionNode,
    LiteralNode,
    ParameterRefNode,
    TypeKind,
)

UNKNOWN = DataType(kind=TypeKind.UNKNOWN)
INTEGRAL = (TypeKind.INTEGER, TypeKind.BIGINT)
FRACTIONAL = (TypeKind.DECIMAL, TypeKind.DOUBLE)


def infer(
    node: ExpressionNode,
    columns: Mapping[str, DataType],
    parameters: Mapping[str, DataType],
) -> DataType:
    if isinstance(node, LiteralNode):
        return node.type
    if isinstance(node, ColumnRefNode):
        return columns.get(node.name, UNKNOWN)
    if isinstance(node, ParameterRefNode):
        return parameters.get(node.parameter_id, UNKNOWN)
    if isinstance(node, CastNode):
        return node.to
    if isinstance(node, CallNode) and node.function in CATALOG:
        return result_type(node.function, [infer(a, columns, parameters) for a in node.args])
    return UNKNOWN


def rounds_on_cast(source: DataType, target: DataType) -> bool:
    """Canonical casts to an integral type round half away from zero (engines differ:
    some truncate), so emitters must round explicitly when the source is fractional."""
    return target.kind in INTEGRAL and source.kind in FRACTIONAL
