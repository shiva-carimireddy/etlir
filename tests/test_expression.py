"""PowerCenter expression subset -> canonical AST, including fail-closed cases."""

from __future__ import annotations

import pytest

from etlir.canonical.model import (
    CallNode,
    CastNode,
    DataType,
    ExpressionNode,
    LiteralNode,
    OpaqueNode,
    ParameterRefNode,
    TypeKind,
)
from etlir.sources.powercenter.expression import Env, translate

S = DataType(kind=TypeKind.STRING)
D = DataType(kind=TypeKind.DECIMAL, precision=12, scale=2)
I = DataType(kind=TypeKind.INTEGER)  # noqa: E741
ENV = Env(
    columns={"NAME": S, "AMT": D, "QTY": I},
    parameters={"LIMIT": ("prm:limit", D)},
    stateful={"COUNTER"},
)


def shape(node: ExpressionNode) -> object:
    if isinstance(node, CallNode):
        return (node.function, *[shape(a) for a in node.args])
    if isinstance(node, CastNode):
        return ("cast", node.to.kind.value, shape(node.arg))
    if isinstance(node, LiteralNode):
        return node.value
    if isinstance(node, ParameterRefNode):
        return f"${node.parameter_id}"
    return getattr(node, "name", node.node)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AMT - QTY * 2", ("subtract", "AMT", ("multiply", "QTY", 2))),
        ("(AMT - QTY) * 2", ("multiply", ("subtract", "AMT", "QTY"), 2)),
        ("NAME || '-' || NAME", ("concat", "NAME", "-", "NAME")),
        ("IIF(ISNULL(AMT), 0, AMT)", ("if", ("is_null", "AMT"), 0, "AMT")),
        ("NOT ISNULL(NAME) AND QTY > 1", ("and", ("not", ("is_null", "NAME")), ("gt", "QTY", 1))),
        ("QTY", "QTY"),
        ("AMT >= $$LIMIT", ("ge", "AMT", "$prm:limit")),
        ("-1.50", "-1.50"),
        ("SUBSTR(NAME, -2, 2)", ("substr", "NAME", -2, 2)),
        ("ltrim(rtrim(name)) -- comment\r\n", ("ltrim", ("rtrim", "NAME"))),
        (
            "IIF(QTY > 1, 1, 0) + (QTY > 2)",
            ("add", ("if", ("gt", "QTY", 1), 1, 0), ("cast", "integer", ("gt", "QTY", 2))),
        ),
    ],
)
def test_supported_subset(text: str, expected: object) -> None:
    t = translate(text, ENV)
    assert not t.opaque, getattr(t.node, "reason", None)
    assert shape(t.node) == expected


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("NOT NAME = NAME", "precedence is ambiguous"),
        ("IIF(QTY > 1, 1)", "without an explicit else"),
        ("NAME = 1", "comparison between string and integer"),
        ("DECODE(QTY, 1, 'a', 'b')", "not in the supported subset"),
        (":LKP.LKP_X(NAME)", "unconnected lookup"),
        ("SYSDATE", "depends on the run time"),
        ("$PMSessionName", "built-in variable"),
        ("$$COUNTER + 1", "carries state"),
        ("$$UNDECLARED", "not declared"),
        ("QTY % 2", "modulus"),
        ("SUM(AMT)", "outside an Aggregator"),
        ("MISSING_PORT + 1", "unknown port"),
        ("", "empty expression"),
        ("AMT +", "unexpected"),
    ],
)
def test_outside_the_subset_is_opaque(text: str, reason: str) -> None:
    t = translate(text, ENV)
    assert isinstance(t.node, OpaqueNode)
    assert t.node.text == text and t.node.dialect == "powercenter"
    assert reason in (t.node.reason or "")


def test_aggregate_context() -> None:
    env = Env(columns={"K": S, "AMT": D}, allow_aggregates=True, group_keys={"K"})
    assert shape(translate("SUM(AMT)", env).node) == ("sum", "AMT")
    assert shape(translate("COUNT(*)", env).node) == ("count_all",)
    assert "neither aggregated nor a group key" in (translate("AMT", env).node.reason or "")  # type: ignore[union-attr]
    assert "nested" in (translate("SUM(SUM(AMT))", env).node.reason or "")  # type: ignore[union-attr]


def test_division_is_marked_as_an_approximation() -> None:
    assert translate("AMT / QTY", ENV).notes == {"pc.expr.divide"}
