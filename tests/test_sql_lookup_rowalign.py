"""SQL override translation, row-aligned fusion, lookups and mapping variables."""

from __future__ import annotations

import pytest

from etlir.canonical.graph import slot_inputs
from etlir.canonical.invariants import validate
from etlir.canonical.model import (
    CanonicalDocument,
    DataType,
    OpaqueNode,
    ParameterRefNode,
    TypeKind,
)
from etlir.sources.powercenter.expression import Env, translate
from etlir.sources.powercenter.sql import (
    SqlUnsupported,
    Translator,
    parse_select,
    plan_select,
)
from tests.conftest import case_doc

INT = DataType(kind=TypeKind.INTEGER)
STR = DataType(kind=TypeKind.STRING)
DEC = DataType(kind=TypeKind.DECIMAL, precision=10, scale=2)
TABLES = {
    "EMP": {"EMP_ID": INT, "NAME": STR, "DEPT_ID": INT, "SALARY": DEC},
    "DEPT": {"DEPT_ID": INT, "DEPT_NAME": STR},
}


def plan(sql: str):  # type: ignore[no-untyped-def]
    return plan_select(parse_select(sql, "oracle"), lambda n: TABLES.get(n.upper()))


# ----------------------------------------------------------------------------- SQL


def test_oracle_outer_join_and_filters_are_separated() -> None:
    q = plan(
        "SELECT E.EMP_ID, NVL(D.DEPT_NAME, 'X') FROM EMP E, DEPT D "
        "WHERE E.DEPT_ID = D.DEPT_ID(+) AND E.SALARY > 10 ORDER BY 1"
    )
    assert [t.alias for t in q.tables] == ["E", "D"]
    assert [k for k, _ in q.joins] == ["left"]
    assert len(q.filters) == 1 and q.notes == ["ORDER BY ignored"]


def test_ansi_join_and_aggregate() -> None:
    q = plan(
        "SELECT EMP.DEPT_ID, COUNT(*), SUM(EMP.SALARY) FROM EMP "
        "INNER JOIN DEPT ON EMP.DEPT_ID = DEPT.DEPT_ID GROUP BY EMP.DEPT_ID"
    )
    assert q.aggregate and [k for k, _ in q.joins] == ["inner"]


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("SELECT * FROM EMP", "SELECT *"),
        ("SELECT EMP_ID FROM EMP, DEPT", "cartesian"),
        ("SELECT EMP_ID FROM (SELECT EMP_ID FROM EMP)", "Subquery"),
        ("SELECT EMP_ID FROM EMP WHERE EMP_ID IN (SELECT DEPT_ID FROM DEPT)", "Subquery"),
        ("SELECT EMP_ID FROM EMP FETCH FIRST 1 ROW ONLY", "LIMIT"),
        ("SELECT X FROM OTHER", "not a known source definition"),
        ("SELECT EMP_ID FROM EMP GROUP BY EMP_ID HAVING COUNT(*) > 1", "HAVING"),
        ("SELECT EMP_ID FROM EMP WHERE {EMP_ID = 1}", "outer-join syntax"),
        ("SELECT EMP_ID FROM EMP WHERE X = $PMSessionName", "$PM"),
    ],
)
def test_outside_the_sql_subset(sql: str, reason: str) -> None:
    with pytest.raises(SqlUnsupported, match=reason.replace("$", r"\$").replace("*", r"\*")):
        plan(sql)


def test_parameters_in_sql() -> None:
    q = plan(
        "SELECT EMP_ID FROM EMP WHERE DEPT_ID = $$D AND NAME = '$$N' "
        "AND EMP_ID > TO_NUMBER('$$S') AND DEPT_ID < $$TXT"
    )
    params = {"D": ("prm:d", INT), "N": ("prm:n", STR), "S": ("prm:s", STR), "TXT": ("prm:t", STR)}
    tr = Translator(q, params, "oracle")
    nodes = [tr.bool_(c)[0] for c in q.filters]
    dumped = " ".join(n.model_dump_json() for n in nodes)
    for pid in ("prm:d", "prm:n", "prm:s", "prm:t"):
        assert pid in dumped
    # An unquoted string parameter next to a number is read as a number (explicit cast).
    assert '"cast"' in dumped
    with pytest.raises(SqlUnsupported, match="not declared"):
        Translator(q, {}, "oracle").bool_(q.filters[0])


def test_oracle_empty_string_is_null() -> None:
    q = plan("SELECT NVL(NAME, '') FROM EMP")
    node, _ = Translator(q, {}, "oracle").expr(q.items[0][1])
    assert '"value":null' in node.model_dump_json().replace(" ", "")


# ------------------------------------------------------------------- lookups / :LKP


def test_lookup_call_is_opaque_without_a_resolver() -> None:
    t = translate(":LKP.LKP_X(A)", Env(columns={"A": STR}))
    assert isinstance(t.node, OpaqueNode) and "not supported here" in (t.node.reason or "")


def test_lookups_case_normalizes_and_fuses() -> None:
    doc = case_doc("pc-lookups")
    assert [d for d in validate(doc) if d.severity.value == "error"] == []
    [df] = doc.dataflows
    lookups = [o for o in df.operations if o.spec.kind == "lookup"]
    assert sorted(o.spec.on_multiple_match for o in lookups) == ["any", "any", "any", "error"]
    # Row-aligned merge fused: every slot has exactly one upstream.
    for op in df.operations:
        assert all(len(ups) == 1 for ups in slot_inputs(df, op.id).values())
    # The unconnected lookup is called twice with the same argument: one lookup operation.
    assert sum(1 for o in lookups if o.id.endswith(":lkp1")) == 1
    assert not any(o.id.endswith(":lkp2") for o in lookups)


# ------------------------------------------------------------------ mapping variables


def test_unmodified_mapping_variable_is_a_run_parameter() -> None:
    doc = case_doc("pc-sql-overrides")
    [param] = doc.parameters
    assert param.name == "$$MIN_HIRE_YEAR" and param.default is None
    assert param.source.rule == "pc.parameter.mapping-variable"
    refs = [
        n
        for df in doc.dataflows
        for e in df.expressions
        for n in [e.ast]
        if isinstance(n, ParameterRefNode)
    ]
    assert refs or any(
        "prm:" in e.ast.model_dump_json() for df in doc.dataflows for e in df.expressions
    )


def test_dataflows_are_named_after_sessions() -> None:
    doc: CanonicalDocument = case_doc("pc-orders")
    assert sorted(df.name for df in doc.dataflows) == [
        "s_m_CUSTOMER_REVENUE",
        "s_m_LOAD_DIM_CUSTOMER",
        "s_m_LOAD_FACT_ORDERS",
    ]
