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
TS = DataType(kind=TypeKind.TIMESTAMP)
ENV = Env(
    columns={"NAME": S, "AMT": D, "QTY": I, "DT": TS},
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
        # IIF without an else value: 0 for numbers, '' for strings, NULL otherwise.
        ("IIF(QTY > 1, AMT)", ("if", ("gt", "QTY", 1), "AMT", 0)),
        ("IIF(QTY > 1, NAME)", ("if", ("gt", "QTY", 1), "NAME", "")),
        ("IIF(QTY > 1, DT)", ("if", ("gt", "QTY", 1), "DT", None)),
        ("DECODE(QTY, 1, 'a', 'b')", ("case", ("eq", "QTY", 1), "a", "b")),
        (
            "DECODE(QTY, 1, 'a', 2, 'b')",
            ("case", ("eq", "QTY", 1), "a", ("eq", "QTY", 2), "b", None),
        ),
        ("DECODE(TRUE, QTY > 1, 'a', 'b')", ("case", ("gt", "QTY", 1), "a", "b")),
        ("IN(QTY, 1, 2)", ("or", ("eq", "QTY", 1), ("eq", "QTY", 2))),
        ("IN(NAME, '84', '85')", ("or", ("eq", "NAME", "84"), ("eq", "NAME", "85"))),
        ("IN(NAME, 'a', 0)", ("eq", ("upper", "NAME"), ("upper", "a"))),
        ("IN(NAME, 'a', 1)", ("eq", "NAME", "a")),
        ("SIGN(AMT)", ("sign", "AMT")),
        ("LPAD(QTY, 4, '0')", ("lpad", ("to_string", "QTY"), 4, "0")),
        ("RPAD(NAME, 3)", ("rpad", "NAME", 3, " ")),
        ("INSTR(NAME, 'x', 2)", ("instr", "NAME", "x", 2)),
        ("REPLACECHR(0, NAME, 'a-', NULL)", ("translate", "NAME", "aA-", "")),
        ("REPLACECHR(1, NAME, 'ab', 'xy')", ("translate", "NAME", "ab", "xx")),
        ("REPLACESTR(0, NAME, 'ab', '_')", ("replace_ci", "NAME", "ab", "_")),
        ("REPLACESTR(1, NAME, 'ab', NULL)", ("replace", "NAME", "ab", "")),
        ("CHR(13) || CHR(10)", ("concat", ("chr", 13), ("chr", 10))),
        ("IS_NUMBER(NAME)", ("matches_number", "NAME")),
        ("IS_SPACES(NAME)", ("is_whitespace", "NAME")),
        ("IS_DATE(NAME, 'yyyy-mm-dd')", ("can_parse_timestamp", "NAME", "YYYY-MM-DD")),
        ("TO_DATE(NAME, 'YYYYMMDD')", ("parse_timestamp", "NAME", "YYYYMMDD")),
        ("TO_DATE(DT)", "DT"),
        ("TO_CHAR(DT, 'MM/DD/YYYY')", ("format_timestamp", "DT", "MM/DD/YYYY")),
        ("TO_CHAR(QTY)", ("to_string", "QTY")),
        ("TO_CHAR(AMT)", ("to_string", "AMT")),
        ("TO_CHAR(DT, 'YYMMDD')", ("format_timestamp", "DT", "YYMMDD")),
        ("TO_DECIMAL(NAME, 2)", ("cast", "decimal", ("leading_decimal", "NAME", 2))),
        ("TO_DECIMAL(AMT, 1)", ("cast", "decimal", ("round", "AMT", 1))),
        ("TO_INTEGER(NAME)", ("cast", "integer", ("leading_decimal", "NAME", 18))),
        ("TO_INTEGER(AMT, TRUE)", ("cast", "integer", ("trunc", "AMT", 0))),
        ("TRUNC(AMT)", ("trunc", "AMT", 0)),
        ("TRUNC(DT, 'MM')", ("trunc_timestamp", "DT", "month")),
        ("ROUND(AMT, 1)", ("round", "AMT", 1)),
        ("ADD_TO_DATE(DT, 'DD', -1)", ("add_interval", "DT", "day", -1)),
        ("GET_DATE_PART(DT, 'YYYY')", ("timestamp_part", "DT", "year")),
        (
            "IIF(QTY < 0, ABORT('bad qty'), QTY)",
            ("if", ("lt", "QTY", 0), ("fail", "bad qty"), "QTY"),
        ),
        ("SETVARIABLE($$LIMIT, AMT)", "AMT"),
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
        ("IIF(QTY > 1, 1, 'a')", "IIF results differ"),
        ("DECODE(QTY, NULL, 'a')", "NULL search value"),
        ("IN(NAME, 'a', 'B')", "without a CaseFlag"),
        ("IN(QTY, 1, NULL)", "containing NULL"),
        ("REPLACESTR(1, NAME, 'a', 'b', 'c')", "several search strings"),
        ("TO_CHAR(DT, 'HH:MI')", "is not supported"),
        ("TO_DATE(NAME, 'DD-MON-YY')", "is not supported"),
        ("TO_DATE(NAME, 'YYMMDD')", "is not supported"),
        ("INSTR(NAME, 'a', -1)", "backward search"),
        ("ROUND(AMT, -1)", "negative precision"),
        ("SETVARIABLE($$LIMIT, NULL)", "current value"),
        ("LPAD(NAME, 3, '')", "empty or NULL pad"),
        ("NAME = 1", "comparison between string and integer"),
        ("SOUNDEX(NAME)", "not in the supported subset"),
        (":LKP.LKP_X(NAME)", "unconnected lookup"),
        ("SYSDATE", "depends on the run time"),
        ("$PMSessionName", "built-in variable"),
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


def test_notes_mark_approximations() -> None:
    assert (
        "pc.expr.mapping-variable"
        in translate(
            "$$LIMIT", Env(columns={}, parameters={"LIMIT": ("prm:limit", D)}, stateful={"LIMIT"})
        ).notes
    )
    assert "pc.expr.to-date" in translate("TO_DATE(NAME, 'YYYY-MM-DD')", ENV).notes
    assert "pc.expr.default-date-format" in translate("TO_DATE(NAME)", ENV).notes
    assert "pc.expr.setvariable" in translate("SETVARIABLE($$LIMIT, AMT)", ENV).notes


def test_builtins_resolve_from_env() -> None:
    env = Env(
        columns={},
        builtins={
            "SESSSTARTTIME": (ParameterRefNode(parameter_id="prm:start"), TS),
            "SYSDATE": (ParameterRefNode(parameter_id="prm:start"), TS),
            "$PMMAPPINGNAME": (LiteralNode(value="m_x", type=S), S),
        },
    )
    assert shape(translate("SESSSTARTTIME", env).node) == "$prm:start"
    t = translate("SYSDATE", env)
    assert shape(t.node) == "$prm:start" and "pc.expr.sysdate" in t.notes
    assert shape(translate("$PMMappingName", env).node) == "m_x"
    assert translate("$PMWorkflowName", env).opaque
