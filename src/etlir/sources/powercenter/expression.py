"""PowerCenter transformation-language subset -> typed canonical expression AST.

Grammar (operator precedence, highest first, as documented for the PowerCenter
transformation language):

    ( )
    unary + - NOT
    * / %
    + -
    ||
    < <= > >=
    = <> != ^=
    AND
    OR

Anything outside the supported subset (unknown functions, built-in variables, unconnected
lookups, ``%``, stateful variable ports, type mismatches, etc.) yields an opaque node with
a reason. The whole expression is then opaque, so a target cannot run part of it.

Fail-closed rule: ``NOT`` directly used as an operand of a non-logical operator (for
example ``NOT A = B``) is reported as ambiguous instead of being guessed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from etlir.canonical.functions import NUMERIC, common_type, result_type
from etlir.canonical.model import (
    CallNode,
    CastNode,
    ColumnRefNode,
    DataType,
    ExpressionNode,
    LiteralNode,
    OpaqueNode,
    ParameterRefNode,
    TypeKind,
)

DIALECT = "powercenter"

# Rule notes that lower evidence confidence for expressions that use them.
APPROXIMATION_NOTES = {
    "pc.expr.divide": "Division-by-zero behavior is taken from the canonical catalog "
    "(NULL); PowerCenter behavior was not verified against a live runtime.",
}


class Opaque(Exception):
    pass


@dataclass
class Env:
    columns: dict[str, DataType] = field(default_factory=dict)
    inline: dict[str, tuple[ExpressionNode, DataType]] = field(default_factory=dict)
    pending: set[str] = field(default_factory=set)
    parameters: dict[str, tuple[str, DataType]] = field(default_factory=dict)
    stateful: set[str] = field(default_factory=set)
    allow_aggregates: bool = False
    group_keys: set[str] | None = None
    # Resolves an unconnected lookup call :LKP.name(args) to a column (see normalize).
    lookup_call: (
        Callable[[str, list[tuple[ExpressionNode, DataType]]], tuple[ExpressionNode, DataType]]
        | None
    ) = None

    def lookup(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for name in [*self.columns, *self.inline, *self.pending]:
            index.setdefault(name.upper(), name)
        return index


@dataclass
class Translation:
    node: ExpressionNode
    type: DataType
    notes: set[str]

    @property
    def opaque(self) -> bool:
        return isinstance(self.node, OpaqueNode)


# ------------------------------------------------------------------------------ lexer

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comment>(--|//)[^\n]*)
  | (?P<str>'[^']*')
  | (?P<num>\d+\.\d*|\.\d+|\d+)
  | (?P<external>:[A-Za-z]+\.)
  | (?P<param>\$\$[A-Za-z0-9_]+)
  | (?P<builtin>\$[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<id>[A-Za-z_][A-Za-z0-9_#$@]*)
  | (?P<op>\|\||<=|>=|<>|!=|\^=|[-+*/%=<>(),:])
    """,
    re.X,
)


def _tokens(text: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            raise Opaque(f"unexpected character {text[pos]!r} at offset {pos}")
        kind = m.lastgroup or ""
        if kind not in ("ws", "comment"):
            out.append((kind, m.group()))
        pos = m.end()
    out.append(("eof", ""))
    return out


# ----------------------------------------------------------------------------- parser

Ast = tuple[Any, ...]
_KEYWORDS = {"AND", "OR", "NOT", "NULL", "TRUE", "FALSE"}
_BUILTINS = {
    "SYSDATE",
    "SESSSTARTTIME",
    "SYSTIMESTAMP",
    "WORKFLOWSTARTTIME",
    "PROC_RESULT",
    "SPOUTPUT",
}


class _Parser:
    def __init__(self, text: str) -> None:
        self.toks = _tokens(text)
        self.i = 0

    def peek(self) -> tuple[str, str]:
        return self.toks[self.i]

    def take(self) -> tuple[str, str]:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def is_op(self, *ops: str) -> bool:
        kind, val = self.peek()
        return kind == "op" and val in ops

    def is_kw(self, kw: str) -> bool:
        kind, val = self.peek()
        return kind == "id" and val.upper() == kw

    def expect(self, op: str) -> None:
        if not self.is_op(op):
            raise Opaque(f"expected {op!r}, found {self.peek()[1]!r}")
        self.take()

    def parse(self) -> Ast:
        node = self.or_()
        if self.peek()[0] != "eof":
            raise Opaque(f"unexpected {self.peek()[1]!r}")
        return node

    def _binary(self, sub: Any, ops: tuple[str, ...], kw: bool = False) -> Ast:
        left: Ast = sub()
        while (kw and any(self.is_kw(o) for o in ops)) or (not kw and self.is_op(*ops)):
            op = self.take()[1].upper()
            left = ("bin", op, left, sub())
        return left

    def or_(self) -> Ast:
        return self._binary(self.and_, ("OR",), kw=True)

    def and_(self) -> Ast:
        return self._binary(self.eq, ("AND",), kw=True)

    def eq(self) -> Ast:
        return self._binary(self.rel, ("=", "<>", "!=", "^="))

    def rel(self) -> Ast:
        return self._binary(self.concat, ("<", "<=", ">", ">="))

    def concat(self) -> Ast:
        return self._binary(self.add, ("||",))

    def add(self) -> Ast:
        return self._binary(self.mul, ("+", "-"))

    def mul(self) -> Ast:
        return self._binary(self.unary, ("*", "/", "%"))

    def unary(self) -> Ast:
        if self.is_op("+", "-"):
            return ("un", self.take()[1], self.unary())
        if self.is_kw("NOT"):
            self.take()
            return ("un", "NOT", self.unary())
        return self.primary()

    def primary(self) -> Ast:
        kind, val = self.take()
        if kind == "num":
            return ("num", val)
        if kind == "str":
            return ("str", val[1:-1])
        if kind == "param":
            return ("param", val)
        if kind == "builtin":
            raise Opaque(f"built-in variable {val} is not supported")
        if kind == "external":
            if val.upper() != ":LKP.":
                raise Opaque(f"{val[1:-1]} call (stored procedure or mapplet) is not supported")
            k2, name = self.take()
            if k2 != "id":
                raise Opaque("malformed :LKP call")
            self.expect("(")
            call_args: list[Ast] = []
            if not self.is_op(")"):
                call_args.append(self.or_())
                while self.is_op(","):
                    self.take()
                    call_args.append(self.or_())
            self.expect(")")
            return ("lkp", name, call_args)
        if kind == "op" and val == "(":
            inner = self.or_()
            self.expect(")")
            return ("paren", inner)
        if kind == "op" and val == ":":
            raise Opaque("unconnected lookup / stored procedure call is not supported")
        if kind == "id":
            up = val.upper()
            if up == "NULL":
                return ("null",)
            if up in ("TRUE", "FALSE"):
                return ("bool", up == "TRUE")
            if up in _KEYWORDS:
                raise Opaque(f"unexpected keyword {val}")
            if up in _BUILTINS and not self.is_op("("):
                raise Opaque(f"built-in {up} depends on the run time and is not supported")
            if self.is_op("("):
                self.take()
                args: list[Ast] = []
                if up == "COUNT" and self.is_op("*"):
                    self.take()
                    self.expect(")")
                    return ("call", "COUNT*", [])
                if not self.is_op(")"):
                    args.append(self.or_())
                    while self.is_op(","):
                        self.take()
                        args.append(self.or_())
                self.expect(")")
                return ("call", up, args)
            return ("id", val)
        raise Opaque(f"unexpected {val or 'end of expression'!r}")


# -------------------------------------------------------------------------- converter

_T = DataType
BOOL, STRING, INT = _T(kind=TypeKind.BOOLEAN), _T(kind=TypeKind.STRING), _T(kind=TypeKind.INTEGER)
UNKNOWN = _T(kind=TypeKind.UNKNOWN)

_ARITH = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}
_CMP = {"=": "eq", "<>": "ne", "!=": "ne", "^=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
_STRING_FNS = {"UPPER": "upper", "LOWER": "lower", "LTRIM": "ltrim", "RTRIM": "rtrim"}
_AGGS = {"SUM": "sum", "MIN": "min", "MAX": "max", "AVG": "avg", "COUNT": "count"}

Typed = tuple[ExpressionNode, DataType]


def _is_null_literal(t: Typed) -> bool:
    return isinstance(t[0], LiteralNode) and t[0].value is None


class _Converter:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.index = env.lookup()
        self.notes: set[str] = set()
        self.in_agg = False

    def call(self, fn: str, *args: Typed) -> Typed:
        node = CallNode(function=fn, args=[a[0] for a in args])
        return node, result_type(fn, [a[1] for a in args])

    def as_bool(self, t: Typed) -> Typed:
        if t[1].kind in (TypeKind.BOOLEAN, TypeKind.UNKNOWN):
            return t
        if t[1].kind in NUMERIC:
            self.notes.add("pc.expr.numeric-condition")
            zero: Typed = (LiteralNode(value=0, type=INT), INT)
            return self.call("ne", t, zero)
        raise Opaque(f"{t[1].kind.value} value used as a condition")

    def as_num(self, t: Typed) -> Typed:
        if t[1].kind in NUMERIC or _is_null_literal(t):
            return t
        if t[1].kind is TypeKind.BOOLEAN:
            return CastNode(to=INT, arg=t[0]), INT
        raise Opaque(f"{t[1].kind.value} value used in arithmetic")

    def as_str(self, t: Typed) -> Typed:
        if t[1].kind is TypeKind.STRING or _is_null_literal(t):
            return t
        raise Opaque(f"{t[1].kind.value} value used as a string (implicit conversion)")

    def conv(self, a: Ast) -> Typed:
        tag = a[0]
        if tag == "paren":
            return self.conv(a[1])
        if tag == "num":
            return self.number(a[1])
        if tag == "str":
            return LiteralNode(value=a[1], type=STRING), STRING
        if tag == "null":
            return LiteralNode(value=None, type=UNKNOWN), UNKNOWN
        if tag == "bool":
            return LiteralNode(value=a[1], type=BOOL), BOOL
        if tag == "param":
            key = a[1][2:].upper()
            if key in self.env.stateful:
                raise Opaque(f"mapping variable {a[1]} carries state between runs")
            if key not in self.env.parameters:
                raise Opaque(f"parameter {a[1]} is not declared in the mapping")
            pid, ptype = self.env.parameters[key]
            return ParameterRefNode(parameter_id=pid), ptype
        if tag == "id":
            return self.ident(a[1])
        if tag == "un":
            return self.unary(a[1], a[2])
        if tag == "bin":
            return self.binary(a[1], a[2], a[3])
        if tag == "call":
            return self.function(a[1], a[2])
        if tag == "lkp":
            if self.env.lookup_call is None or self.in_agg:
                raise Opaque("unconnected lookup call is not supported here")
            return self.env.lookup_call(a[1], [self.conv(x) for x in a[2]])
        raise Opaque(f"unsupported syntax {tag}")

    def number(self, text: str) -> Typed:
        if "." not in text:
            n = int(text)
            t = INT if n < 2**31 else _T(kind=TypeKind.BIGINT)
            if n >= 2**63:
                raise Opaque("integer literal out of range")
            return LiteralNode(value=n, type=t), t
        try:
            d = Decimal(text)
        except InvalidOperation as exc:
            raise Opaque(f"bad number {text}") from exc
        _, digits, exp = d.as_tuple()
        scale = max(0, -int(exp))
        precision = max(len(digits), scale + 1)
        t = _T(kind=TypeKind.DECIMAL, precision=min(precision, 38), scale=scale)
        return LiteralNode(value=str(d), type=t), t

    def ident(self, name: str) -> Typed:
        actual = self.index.get(name.upper())
        if actual is None:
            raise Opaque(f"unknown port {name}")
        if actual in self.env.inline:
            node, typ = self.env.inline[actual]
            if isinstance(node, OpaqueNode):
                raise Opaque(f"depends on variable port {actual}: {node.reason}")
            return node, typ
        if actual in self.env.pending:
            raise Opaque(f"variable port {actual} is read before it is set (stateful)")
        keys = self.env.group_keys
        if keys is not None and not self.in_agg and actual not in keys:
            raise Opaque(f"port {actual} is neither aggregated nor a group key")
        return ColumnRefNode(name=actual), self.env.columns[actual]

    def unary(self, op: str, arg: Ast) -> Typed:
        if op == "NOT":
            return self.call("not", self.as_bool(self.conv(arg)))
        t = self.as_num(self.conv(arg))
        if op == "+":
            return t
        node = t[0]
        if (
            isinstance(node, LiteralNode)
            and isinstance(node.value, (int, str))
            and node.value is not None
            and t[1].kind in NUMERIC
        ):
            value = node.value
            neg: int | str = -value if isinstance(value, int) else str(-Decimal(value))
            return LiteralNode(value=neg, type=t[1]), t[1]
        return self.call("negate", t)

    def binary(self, op: str, left: Ast, right: Ast) -> Typed:
        if op not in ("AND", "OR") and ("un", "NOT") in ((left[:2]), (right[:2])):
            raise Opaque(
                "NOT used as an operand of a comparison or arithmetic operator; "
                "precedence is ambiguous (parenthesize)"
            )
        if op in ("AND", "OR"):
            return self.call(
                op.lower(), self.as_bool(self.conv(left)), self.as_bool(self.conv(right))
            )
        if op == "%":
            raise Opaque("modulus operator % is not supported")
        if op in _ARITH:
            lt, rt = self.as_num(self.conv(left)), self.as_num(self.conv(right))
            if op == "/":
                self.notes.add("pc.expr.divide")
            return self.call(_ARITH[op], lt, rt)
        if op == "||":
            parts = [self.as_str(self.conv(x)) for x in self.flatten_concat(left, right)]
            return self.call("concat", *parts)
        lt, rt = self.conv(left), self.conv(right)
        lt, rt = self.comparable(lt, rt)
        return self.call(_CMP[op], lt, rt)

    def flatten_concat(self, left: Ast, right: Ast) -> list[Ast]:
        out: list[Ast] = []
        for side in (left, right):
            if side[0] == "bin" and side[1] == "||":
                out.extend(self.flatten_concat(side[2], side[3]))
            else:
                out.append(side)
        return out

    def comparable(self, lt: Typed, rt: Typed) -> tuple[Typed, Typed]:
        if _is_null_literal(lt) or _is_null_literal(rt):
            return lt, rt
        kinds = {lt[1].kind, rt[1].kind}
        if kinds <= set(NUMERIC) | {TypeKind.BOOLEAN} and kinds & set(NUMERIC):
            return self.as_num(lt), self.as_num(rt)
        if len(kinds) == 1 and kinds <= {
            TypeKind.STRING,
            TypeKind.TIMESTAMP,
            TypeKind.BOOLEAN,
            *NUMERIC,
        }:
            return lt, rt
        raise Opaque(f"comparison between {lt[1].kind.value} and {rt[1].kind.value}")

    def function(self, name: str, args: list[Ast]) -> Typed:
        n = len(args)
        if name == "IIF":
            if n != 3:
                raise Opaque("IIF without an explicit else value is not supported")
            cond = self.as_bool(self.conv(args[0]))
            a, b = self.conv(args[1]), self.conv(args[2])
            if {a[1].kind, b[1].kind} & {TypeKind.BOOLEAN} and {a[1].kind, b[1].kind} & set(
                NUMERIC
            ):
                a, b = self.as_num(a), self.as_num(b)
            if common_type([a[1], b[1]]).kind is TypeKind.UNKNOWN and not (
                _is_null_literal(a) or _is_null_literal(b)
            ):
                raise Opaque(f"IIF branches differ ({a[1].kind.value}, {b[1].kind.value})")
            return self.call("if", cond, a, b)
        if name == "ISNULL" and n == 1:
            return self.call("is_null", self.conv(args[0]))
        if name in _STRING_FNS and n == 1:
            return self.call(_STRING_FNS[name], self.as_str(self.conv(args[0])))
        if name == "LENGTH" and n == 1:
            return self.call("length", self.as_str(self.conv(args[0])))
        if name == "CONCAT" and n == 2:
            return self.call("concat", *(self.as_str(self.conv(x)) for x in args))
        if name == "SUBSTR" and n in (2, 3):
            s = self.as_str(self.conv(args[0]))
            nums = [self.as_num(self.conv(x)) for x in args[1:]]
            for t in nums:
                if t[1].kind not in (TypeKind.INTEGER, TypeKind.BIGINT, TypeKind.UNKNOWN):
                    raise Opaque("SUBSTR position/length must be integers")
            return self.call("substr", s, *nums)
        if name == "ABS" and n == 1:
            return self.call("abs", self.as_num(self.conv(args[0])))
        if name in _AGGS or name == "COUNT*":
            return self.aggregate(name, args)
        raise Opaque(f"function {name}/{n} is not in the supported subset")

    def aggregate(self, name: str, args: list[Ast]) -> Typed:
        if not self.env.allow_aggregates:
            raise Opaque(f"aggregate function {name} outside an Aggregator")
        if self.in_agg:
            raise Opaque("nested aggregate functions are not supported")
        if name == "COUNT*":
            return self.call("count_all")
        if len(args) != 1:
            raise Opaque(f"{name} with a filter condition is not supported")
        self.in_agg = True
        try:
            arg = self.conv(args[0])
        finally:
            self.in_agg = False
        if name in ("SUM", "AVG"):
            arg = self.as_num(arg)
        return self.call(_AGGS[name], arg)


def translate(text: str, env: Env) -> Translation:
    conv = _Converter(env)
    try:
        if not _tokens(text)[:-1]:
            raise Opaque("empty expression")
        ast = _Parser(text).parse()
        node, typ = conv.conv(ast)
    except Opaque as exc:
        return Translation(OpaqueNode(text=text, dialect=DIALECT, reason=str(exc)), UNKNOWN, set())
    return Translation(node, typ, conv.notes)


__all__ = ["APPROXIMATION_NOTES", "DIALECT", "Env", "Translation", "translate"]
