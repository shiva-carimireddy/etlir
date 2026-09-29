"""SQL override translation (Source Qualifier and Lookup overrides, source filters,
user-defined joins) into canonical relational steps.

Parsing uses SQLGlot with the source database's dialect. The accepted subset is:

* ``SELECT [DISTINCT] <items> FROM <tables> [JOIN … ON …] [WHERE …] [ORDER BY …]`` over
  tables that resolve to known source definitions;
* inner joins (comma joins with join predicates in WHERE, ``[INNER] JOIN … ON``), left
  joins (``LEFT [OUTER] JOIN … ON`` and Oracle ``(+)`` markers);
* expressions: columns, literals, ``NULL``, arithmetic, ``||``, comparisons,
  ``AND/OR/NOT``, ``IS [NOT] NULL``, ``[NOT] IN (list)``, ``BETWEEN``, ``CASE WHEN``,
  ``NVL``/``COALESCE``, ``UPPER``, ``LOWER``, ``TRIM``/``LTRIM``/``RTRIM`` (spaces),
  ``LENGTH``, ``SUBSTR``, and mapping parameters ``$$NAME`` (numeric) or ``'$$NAME'``
  (string, or inside ``TO_NUMBER`` for numeric parameters).

Everything else (aggregates, GROUP BY, subqueries, set operations, FETCH/LIMIT, window
functions, other functions, cross joins, ``SELECT *``) raises :class:`SqlUnsupported`
with a reason. ORDER BY is ignored: row order is not part of canonical semantics.

Oracle rules applied: the empty string literal is NULL, ``||`` ignores NULLs. For other
dialects ``||`` is NULL-propagating.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from etlir.canonical.functions import NUMERIC, result_type
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

PARAM = "ETLIR_PARAM__"
QPARAM = "ETLIR_QPARAM__"
UNKNOWN = DataType(kind=TypeKind.UNKNOWN)
BOOL = DataType(kind=TypeKind.BOOLEAN)
STRING = DataType(kind=TypeKind.STRING)
DECIMAL = DataType(kind=TypeKind.DECIMAL, precision=38, scale=10)

_DIALECTS = {
    "oracle": "oracle",
    "microsoft sql server": "tsql",
    "sybase": "tsql",
    "db2": "postgres",
    "teradata": "teradata",
    "odbc": "",
}


class SqlUnsupported(Exception):
    pass


def dialect_for(database_type: str) -> str:
    return _DIALECTS.get(database_type.strip().lower(), "")


def _prepare(text: str) -> str:
    if re.search(r"\$PM\w*", text):
        raise SqlUnsupported("session/built-in variables ($PM…) in SQL are not supported")
    text = re.sub(r"'\$\$(\w+)'", lambda m: f"{QPARAM}{m.group(1)}", text)
    text = re.sub(r"\$\$(\w+)", lambda m: f"{PARAM}{m.group(1)}", text)
    if "{" in text:
        raise SqlUnsupported("Informatica outer-join syntax {…} is not supported")
    return text


def parse_select(text: str, dialect: str) -> exp.Select:
    try:
        tree = sqlglot.parse_one(_prepare(text), read=dialect or None)
    except ParseError as exc:
        raise SqlUnsupported(f"SQL does not parse: {str(exc).splitlines()[0]}") from exc
    if not isinstance(tree, exp.Select):
        raise SqlUnsupported(f"{type(tree).__name__} statements are not supported")
    return tree


def parse_condition(text: str, dialect: str) -> exp.Expression:
    select = parse_select(f"SELECT 1 FROM ETLIR_DUAL WHERE {text}", dialect)
    where = select.args.get("where")
    if where is None:
        raise SqlUnsupported("empty condition")
    node: exp.Expression = where.this
    return node


@dataclass
class Table:
    alias: str
    name: str
    columns: dict[str, DataType]  # exact dataset column name -> type


@dataclass
class Query:
    tables: list[Table]
    joins: list[tuple[str, exp.Expression]]  # for tables[1:]: ("inner"|"left", condition)
    filters: list[exp.Expression]
    items: list[tuple[str | None, exp.Expression]]
    distinct: bool
    notes: list[str] = field(default_factory=list)
    group_by: list[exp.Expression] = field(default_factory=list)
    aggregate: bool = False


def _conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node]


def _tables_of(node: exp.Expression, resolve_alias: Callable[[exp.Column], str]) -> set[str]:
    return {
        resolve_alias(c)
        for c in node.find_all(exp.Column)
        if not c.name.upper().startswith((PARAM, QPARAM))
    }


def plan_select(
    select: exp.Select,
    lookup_table: Callable[[str], dict[str, DataType] | None],
    extra_filters: list[exp.Expression] | None = None,
) -> Query:
    """Structure a parsed SELECT into tables, ordered joins, filters and items."""
    for key in (
        "having",
        "qualify",
        "limit",
        "offset",
        "with_",
        "with",
        "windows",
        "connect",
        "prewhere",
    ):
        if select.args.get(key):
            raise SqlUnsupported(f"{key.rstrip('_').upper()} clauses are not supported")
    for node in select.find_all(exp.Subquery, exp.Window, exp.Union):
        raise SqlUnsupported(f"{type(node).__name__} is not supported")
    for star in select.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise SqlUnsupported("SELECT * is not supported")
    where_node0 = select.args.get("where")
    if where_node0 is not None and list(where_node0.find_all(exp.AggFunc)):
        raise SqlUnsupported("aggregate functions in WHERE")
    group = select.args.get("group")
    group_by: list[exp.Expression] = list(group.expressions) if group is not None else []
    if any(not isinstance(g, exp.Column) for g in group_by):
        raise SqlUnsupported("GROUP BY expressions other than columns")
    aggregate = bool(group_by) or any(list(i.find_all(exp.AggFunc)) for i in select.expressions)
    from_ = select.args.get("from_") or select.args.get("from")
    if from_ is None:
        raise SqlUnsupported("query without FROM")
    table_nodes: list[tuple[exp.Table, exp.Join | None]] = []
    if not isinstance(from_.this, exp.Table):
        raise SqlUnsupported("FROM must name a table")
    table_nodes.append((from_.this, None))
    for j in select.args.get("joins") or []:
        if not isinstance(j.this, exp.Table):
            raise SqlUnsupported("JOIN must name a table")
        table_nodes.append((j.this, j))

    tables: list[Table] = []
    for t, _ in table_nodes:
        cols = lookup_table(t.name)
        if cols is None:
            raise SqlUnsupported(f"table {t.name} is not a known source definition")
        tables.append(Table(alias=t.alias_or_name, name=t.name, columns=cols))
    aliases = {t.alias.upper(): t for t in tables}
    if len(aliases) != len(tables):
        raise SqlUnsupported("duplicate table aliases")

    def alias_of(col: exp.Column) -> str:
        if col.table:
            key = col.table.upper()
            if key not in aliases:
                raise SqlUnsupported(f"unknown table qualifier {col.table}")
            return aliases[key].alias
        owners = [t.alias for t in tables if col.name.upper() in {c.upper() for c in t.columns}]
        if len(owners) != 1:
            raise SqlUnsupported(f"column {col.name} is {'ambiguous' if owners else 'unknown'}")
        return owners[0]

    where_node = select.args.get("where")
    where_parts = _conjuncts(where_node.this if where_node is not None else None)
    where_parts += extra_filters or []
    joins: list[tuple[str, exp.Expression]] = []
    joined = {tables[0].alias}
    for (t, j), table in zip(table_nodes[1:], tables[1:], strict=True):
        alias = table.alias
        if j is not None and j.args.get("on") is not None:
            side = (j.args.get("side") or "").upper()
            kind = (j.args.get("kind") or "").upper()
            if side not in ("", "LEFT") or kind not in ("", "INNER", "OUTER"):
                raise SqlUnsupported(f"{side} {kind} JOIN is not supported".strip())
            joins.append(("left" if side == "LEFT" else "inner", j.args["on"]))
        else:
            if j is not None and (j.args.get("using") or j.args.get("side") or j.args.get("kind")):
                raise SqlUnsupported("this JOIN form is not supported")
            conds, rest, kind = [], [], "inner"
            for c in where_parts:
                used = _tables_of(c, alias_of)
                marked = {
                    alias_of(col) for col in c.find_all(exp.Column) if col.args.get("join_mark")
                }
                if alias in used and used <= joined | {alias} and len(used) > 1:
                    if marked and marked != {alias}:
                        raise SqlUnsupported("(+) outer join markers on an earlier table")
                    kind = "left" if marked else kind
                    conds.append(c)
                else:
                    rest.append(c)
            if not conds:
                raise SqlUnsupported(f"table {t.name} has no join condition (cartesian product)")
            where_parts = rest
            combined = cast(exp.Expression, exp.and_(*conds)) if len(conds) > 1 else conds[0]
            joins.append((kind, combined))
        joined.add(alias)
    for c in where_parts:
        if any(col.args.get("join_mark") for col in c.find_all(exp.Column)):
            raise SqlUnsupported("(+) marker outside a join condition")
    items: list[tuple[str | None, exp.Expression]] = []
    for item in select.expressions:
        if isinstance(item, exp.Alias):
            items.append((item.alias, item.this))
        else:
            items.append((item.name if isinstance(item, exp.Column) else None, item))
    notes = ["ORDER BY ignored"] if select.args.get("order") else []
    return Query(
        tables,
        joins,
        where_parts,
        items,
        bool(select.args.get("distinct")),
        notes,
        group_by,
        aggregate,
    )


class Translator:
    """sqlglot expression -> canonical AST over qualified column names ``alias.column``."""

    def __init__(
        self, query: Query, parameters: dict[str, tuple[str, DataType]], dialect: str
    ) -> None:
        self.q = query
        self.params = parameters
        self.oracle = dialect == "oracle"
        self.aliases = {t.alias.upper(): t for t in query.tables}
        self.group_keys: set[str] | None = None  # qualified names, when aggregating
        self.in_agg = False

    def column(self, col: exp.Column) -> tuple[ExpressionNode, DataType]:
        name = col.name
        if name.upper().startswith(QPARAM) or name.upper().startswith(PARAM):
            return self.param(name)
        tables = (
            [self.aliases[col.table.upper()]]
            if col.table and col.table.upper() in self.aliases
            else [t for t in self.q.tables if name.upper() in {c.upper() for c in t.columns}]
        )
        if col.table and col.table.upper() not in self.aliases:
            raise SqlUnsupported(f"unknown table qualifier {col.table}")
        matches = [(t, c) for t in tables for c in t.columns if c.upper() == name.upper()]
        if len(matches) != 1:
            raise SqlUnsupported(f"column {name} is {'ambiguous' if matches else 'unknown'}")
        t, c = matches[0]
        qualified = f"{t.alias}.{c}"
        if self.group_keys is not None and not self.in_agg and qualified not in self.group_keys:
            raise SqlUnsupported(f"column {name} is neither aggregated nor grouped")
        return ColumnRefNode(name=qualified), t.columns[c]

    def param(self, token: str, numeric_context: bool = False) -> tuple[ExpressionNode, DataType]:
        quoted = token.upper().startswith(QPARAM)
        key = token[len(QPARAM if quoted else PARAM) :].upper()
        if key not in self.params:
            raise SqlUnsupported(f"parameter $${key} is not declared in the mapping")
        pid, ptype = self.params[key]
        numeric = ptype.kind in NUMERIC
        if quoted and numeric_context and ptype.kind is TypeKind.STRING:
            return CastNode(to=DECIMAL, arg=ParameterRefNode(parameter_id=pid)), DECIMAL
        if quoted and numeric and not numeric_context:
            raise SqlUnsupported(f"quoted numeric parameter $${key} outside TO_NUMBER")
        if not quoted and not numeric:
            raise SqlUnsupported(f"unquoted parameter $${key} would be substituted as SQL text")
        if quoted and not numeric and ptype.kind is not TypeKind.STRING:
            raise SqlUnsupported(f"parameter $${key} of type {ptype.kind.value} in quotes")
        return ParameterRefNode(parameter_id=pid), ptype

    def operand(
        self, node: exp.Expression, other: exp.Expression
    ) -> tuple[ExpressionNode, DataType]:
        """A comparison operand. An unquoted string parameter is substituted as SQL text;
        next to a numeric operand it reads as a number (explicit string->decimal cast)."""
        if isinstance(node, exp.Column) and node.name.upper().startswith(PARAM):
            key = node.name[len(PARAM) :].upper()
            if key in self.params and self.params[key][1].kind is TypeKind.STRING:
                other_type = self.expr(other)[1]
                if other_type.kind in NUMERIC:
                    ref = ParameterRefNode(parameter_id=self.params[key][0])
                    return CastNode(to=DECIMAL, arg=ref), DECIMAL
        return self.expr(node)

    def aggregate(self, node: exp.AggFunc) -> tuple[ExpressionNode, DataType]:
        names = {"Max": "max", "Min": "min", "Sum": "sum", "Avg": "avg", "Count": "count"}
        fn = names.get(type(node).__name__)
        if fn is None or self.group_keys is None:
            raise SqlUnsupported(f"aggregate {type(node).__name__} is not supported here")
        if self.in_agg:
            raise SqlUnsupported("nested aggregates")
        arg = node.this
        if isinstance(arg, exp.Distinct):
            raise SqlUnsupported("aggregates over DISTINCT values")
        if fn == "count" and isinstance(arg, exp.Star):
            return self.call("count_all")
        self.in_agg = True
        try:
            inner = self.expr(arg)
        finally:
            self.in_agg = False
        if fn in ("sum", "avg") and inner[1].kind not in (*NUMERIC, TypeKind.UNKNOWN):
            raise SqlUnsupported(f"{fn.upper()} of {inner[1].kind.value}")
        return self.call(fn, inner)

    def call(
        self, fn: str, *args: tuple[ExpressionNode, DataType]
    ) -> tuple[ExpressionNode, DataType]:
        return CallNode(function=fn, args=[a[0] for a in args]), result_type(
            fn, [a[1] for a in args]
        )

    def bool_(self, node: exp.Expression) -> tuple[ExpressionNode, DataType]:
        t = self.expr(node)
        if t[1].kind not in (TypeKind.BOOLEAN, TypeKind.UNKNOWN):
            raise SqlUnsupported(f"{type(node).__name__} used as a condition")
        return t

    def comparable(
        self, a: tuple[ExpressionNode, DataType], b: tuple[ExpressionNode, DataType]
    ) -> None:
        ka, kb = a[1].kind, b[1].kind
        if TypeKind.UNKNOWN in (ka, kb):
            return
        if (ka in NUMERIC) != (kb in NUMERIC) or (ka not in NUMERIC and ka is not kb):
            raise SqlUnsupported(
                f"comparison between {ka.value} and {kb.value} (implicit conversion)"
            )

    def expr(self, node: exp.Expression) -> tuple[ExpressionNode, DataType]:
        cmp: dict[type[exp.Expression], str] = {
            exp.EQ: "eq",
            exp.NEQ: "ne",
            exp.LT: "lt",
            exp.LTE: "le",
            exp.GT: "gt",
            exp.GTE: "ge",
        }
        arith: dict[type[exp.Expression], str] = {
            exp.Add: "add",
            exp.Sub: "subtract",
            exp.Mul: "multiply",
            exp.Div: "divide",
        }
        if isinstance(node, exp.Paren):
            return self.expr(node.this)
        if isinstance(node, exp.Column):
            return self.column(node)
        if isinstance(node, exp.Null):
            return LiteralNode(value=None, type=UNKNOWN), UNKNOWN
        if isinstance(node, exp.Boolean):
            return LiteralNode(value=bool(node.this), type=BOOL), BOOL
        if isinstance(node, exp.Literal):
            if node.is_string:
                if node.this == "" and self.oracle:
                    return LiteralNode(value=None, type=STRING), STRING
                return LiteralNode(value=node.this, type=STRING), STRING
            text = node.this
            if "." in text or "e" in text.lower():
                digits = text.replace("-", "").replace(".", "")
                scale = len(text.split(".")[1]) if "." in text else 0
                t = DataType(
                    kind=TypeKind.DECIMAL, precision=max(len(digits), scale + 1), scale=scale
                )
                return LiteralNode(value=text, type=t), t
            t = DataType(kind=TypeKind.INTEGER if abs(int(text)) < 2**31 else TypeKind.BIGINT)
            return LiteralNode(value=int(text), type=t), t
        if isinstance(node, exp.Neg):
            inner = self.expr(node.this)
            return self.call("negate", inner)
        if type(node) in cmp:
            a = self.operand(node.this, node.expression)
            b = self.operand(node.expression, node.this)
            self.comparable(a, b)
            return self.call(cmp[type(node)], a, b)
        if isinstance(node, (exp.And, exp.Or)):
            fn = "and" if isinstance(node, exp.And) else "or"
            return self.call(fn, self.bool_(node.this), self.bool_(node.expression))
        if isinstance(node, exp.Not):
            return self.call("not", self.bool_(node.this))
        if isinstance(node, exp.Is):
            if not isinstance(node.expression, exp.Null):
                raise SqlUnsupported("IS other than IS NULL")
            return self.call("is_null", self.expr(node.this))
        if type(node) in arith:
            a, b = self.expr(node.this), self.expr(node.expression)
            for x in (a, b):
                if x[1].kind not in (*NUMERIC, TypeKind.UNKNOWN):
                    raise SqlUnsupported(f"arithmetic on {x[1].kind.value}")
            return self.call(arith[type(node)], a, b)
        if isinstance(node, exp.DPipe):
            a, b = self.expr(node.this), self.expr(node.expression)
            joined = self.call("concat", a, b)
            if self.oracle:
                return joined
            either_null = self.call("or", self.call("is_null", a), self.call("is_null", b))
            null = (LiteralNode(value=None, type=STRING), STRING)
            return self.call("if", either_null, null, joined)
        if isinstance(node, exp.Coalesce):
            return self.call(
                "coalesce", self.expr(node.this), *(self.expr(x) for x in node.expressions)
            )
        if isinstance(node, exp.Upper):
            return self.call("upper", self.expr(node.this))
        if isinstance(node, exp.Lower):
            return self.call("lower", self.expr(node.this))
        if isinstance(node, exp.Length):
            return self.call("length", self.expr(node.this))
        if isinstance(node, exp.Trim):
            if node.args.get("expression") is not None:
                raise SqlUnsupported("TRIM of characters other than spaces")
            inner = self.expr(node.this)
            position = (node.args.get("position") or "BOTH").upper()
            if position == "LEADING":
                return self.call("ltrim", inner)
            if position == "TRAILING":
                return self.call("rtrim", inner)
            return self.call("ltrim", self.call("rtrim", inner))
        if isinstance(node, exp.Substring):
            args = [self.expr(node.this), self.expr(node.args["start"])]
            if node.args.get("length") is not None:
                args.append(self.expr(node.args["length"]))
            return self.call("substr", *args)
        if isinstance(node, exp.ToNumber) and isinstance(node.this, exp.Column):
            return self.param(node.this.name, numeric_context=True)
        if isinstance(node, exp.AggFunc):
            return self.aggregate(node)
        if isinstance(node, exp.In):
            if node.args.get("query") is not None or not node.expressions:
                raise SqlUnsupported("IN with a subquery")
            left = self.expr(node.this)
            parts = []
            for x in node.expressions:
                right = self.expr(x)
                self.comparable(left, right)
                parts.append(self.call("eq", left, right))
            result = parts[0]
            for p in parts[1:]:
                result = self.call("or", result, p)
            return result
        if isinstance(node, exp.Between):
            v, lo, hi = (
                self.expr(node.this),
                self.expr(node.args["low"]),
                self.expr(node.args["high"]),
            )
            self.comparable(v, lo)
            self.comparable(v, hi)
            return self.call("and", self.call("ge", v, lo), self.call("le", v, hi))
        if isinstance(node, exp.Case):
            if node.this is not None:
                raise SqlUnsupported("simple CASE (CASE x WHEN …)")
            default = (
                self.expr(node.args["default"])
                if node.args.get("default")
                else (LiteralNode(value=None, type=UNKNOWN), UNKNOWN)
            )
            result = default
            for branch in reversed(node.args.get("ifs") or []):
                result = self.call(
                    "if", self.bool_(branch.this), self.expr(branch.args["true"]), result
                )
            return result
        raise SqlUnsupported(f"SQL construct {type(node).__name__} is not supported")
