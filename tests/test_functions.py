"""Canonical function catalog: literal-argument rule and edge cases the truth-table case
(benchmarks/cases/pc-function-semantics) cannot express because its run must succeed."""

from __future__ import annotations

import duckdb
import pytest

from etlir.canonical.functions import CATALOG, arity_ok, format_regex, parse_format
from etlir.canonical.invariants import validate
from etlir.canonical.model import (
    CallNode,
    CanonicalDocument,
    DataType,
    LiteralNode,
    TypeKind,
)
from etlir.targets.duckdb.emitter import lower as duck_lower

S = DataType(kind=TypeKind.STRING)
I = DataType(kind=TypeKind.INTEGER)  # noqa: E741


def lit(value: object, t: DataType = S) -> LiteralNode:
    return LiteralNode(value=value, type=t)  # type: ignore[arg-type]


def duck(fn: str, *args: LiteralNode) -> object:
    sql = duck_lower(CallNode(function=fn, args=list(args)), {})
    return duckdb.sql(f"SELECT {sql}").fetchone()[0]  # type: ignore[index]


def test_every_catalog_function_is_lowered_by_every_emitter() -> None:
    from etlir.targets.duckdb.emitter import manifest as duck_manifest
    from etlir.targets.spark.emitter import manifest as spark_manifest

    for m in (duck_manifest("x"), spark_manifest("x")):
        declared = {r.construct_id for r in m.rules}
        assert {f"function.{name}" for name in CATALOG} <= declared


def test_formats() -> None:
    assert parse_format("YYYYMMDD") == ["YYYY", "MM", "DD"]
    assert parse_format("MM/DD/YYYY HH24:MI:SS") is not None
    for bad in ("DD-MON-YY", "HH:MI", "YYYY-DDD", "", "-"):
        assert parse_format(bad) is None, bad
    assert format_regex(["YYYY", "-", "MM"]) == "^[0-9]{4}-[0-9]{2}$"


def test_case_needs_condition_value_pairs_and_a_default() -> None:
    assert arity_ok("case", 3) and arity_ok("case", 5)
    assert not arity_ok("case", 4) and not arity_ok("case", 1)


def test_parse_timestamp_fails_the_run_on_a_malformed_string() -> None:
    fmt = lit("YYYY-MM-DD")
    assert str(duck("parse_timestamp", lit("2024-01-05"), fmt)) == "2024-01-05 00:00:00"
    assert duck("parse_timestamp", lit(None), fmt) is None
    assert duck("can_parse_timestamp", lit("2024-1-5"), fmt) is False
    with pytest.raises(duckdb.Error, match="cannot parse timestamp"):
        duck("parse_timestamp", lit("2024-1-5"), fmt)


def test_string_edges() -> None:
    assert duck("instr", lit("abc"), lit(""), lit(1, I)) == 0  # empty needle
    assert duck("instr", lit("abcabc"), lit("c"), lit(4, I)) == 6
    assert duck("lpad", lit("ab"), lit(-1, I), lit("x")) == ""
    assert duck("lpad", lit(None), lit(0, I), lit("x")) is None
    assert duck("chr", lit(200, I)) is None
    assert duck("replace_ci", lit("a.B.b"), lit("."), lit("$1\\")) == "a$1\\B$1\\b"
    assert duck("is_whitespace", lit(" \t")) is True
    assert duck("matches_number", lit(" -1.5e3 ")) is True


def test_literal_arguments_are_enforced(toy_doc: CanonicalDocument) -> None:
    df = toy_doc.dataflows[0]
    upper = CallNode(function="upper", args=[lit("x")])

    def with_ast(ast: CallNode) -> list[str]:
        expr = df.expressions[0].model_copy(update={"ast": ast})
        flow = df.model_copy(update={"expressions": [expr, *df.expressions[1:]]})
        return [d.message for d in validate(toy_doc.model_copy(update={"dataflows": [flow]}))]

    assert not any(
        "literal" in m
        for m in with_ast(CallNode(function="lpad", args=[lit("a"), lit(3, I), lit("x")]))
    )
    found = with_ast(CallNode(function="lpad", args=[lit("a"), lit(3, I), upper]))
    assert any("lpad requires literal arguments at positions [2]" in m for m in found)
