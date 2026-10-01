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

from etlir.canonical.functions import NUMERIC, common_type, parse_format, result_type
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
    "pc.expr.sysdate": "SYSDATE is evaluated once, at run start; PowerCenter reads the clock "
    "when the row is processed.",
    "pc.expr.setvariable": "SETVARIABLE returns its value argument. The variable's final value "
    "is not persisted between runs (supply the start value as a run parameter), and a NULL "
    "value yields NULL where PowerCenter returns the variable's current value.",
    "pc.expr.mapping-variable": "A mapping variable changed by SETVARIABLE is read as its start "
    "value, supplied as a run parameter; ETLIR does not persist the final value.",
    "pc.expr.to-date": "A string that does not match the date format fails the run; "
    "PowerCenter rejects the row instead.",
    "pc.expr.default-date-format": "The session date format is assumed to be the PowerCenter "
    "default MM/DD/YYYY HH24:MI:SS.",
    "pc.expr.to-number": "String-to-number conversion uses the documented rule (leading numeric "
    "part, 0 if none); not verified against a live runtime.",
    "pc.expr.number-text": "A fractional decimal converts to its exact plain notation without "
    "trailing zeros; PowerCenter formats at most 15 significant digits (scientific beyond).",
    "pc.expr.abort-message": "ABORT with a computed message fails the run with a fixed message.",
    "pc.expr.empty-output": "An output port with an empty expression yields NULL.",
    "pc.expr.to-decimal-scale": "TO_DECIMAL without a scale is converted with 18 decimal places.",
}

DEFAULT_DATE_FORMAT = "MM/DD/YYYY HH24:MI:SS"

# PowerCenter date format strings -> canonical time units (ADD_TO_DATE, GET_DATE_PART, TRUNC).
_DATE_UNITS = {
    **dict.fromkeys(("Y", "YY", "YYY", "YYYY"), "year"),
    **dict.fromkeys(("MM", "MON", "MONTH"), "month"),
    **dict.fromkeys(("D", "DD", "DDD", "DY", "DAY"), "day"),
    **dict.fromkeys(("HH", "HH12", "HH24"), "hour"),
    "MI": "minute",
    "SS": "second",
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
    # Built-in variables with a known value: SESSSTARTTIME, $PMMappingName, ... (upper case).
    builtins: dict[str, tuple[ExpressionNode, DataType]] = field(default_factory=dict)
    # Turns another $PM... variable (a value of the run environment) into a run parameter.
    pm_parameter: Callable[[str], tuple[ExpressionNode, DataType]] | None = None
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
            return ("builtin", val.upper())
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
                return ("builtin", up)
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
TIMESTAMP = _T(kind=TypeKind.TIMESTAMP)
INTEGRAL = (TypeKind.INTEGER, TypeKind.BIGINT)


def _whole(t: DataType) -> bool:
    """Integer types, and decimals declared with scale 0 (their text form is exact)."""
    return t.kind in INTEGRAL or (
        t.kind is TypeKind.DECIMAL and t.precision is not None and not t.scale
    )


_ARITH = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}
_CMP = {"=": "eq", "<>": "ne", "!=": "ne", "^=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
_STRING_FNS = {"UPPER": "upper", "LOWER": "lower", "LTRIM": "ltrim", "RTRIM": "rtrim"}
_AGGS = {"SUM": "sum", "MIN": "min", "MAX": "max", "AVG": "avg", "COUNT": "count"}

Typed = tuple[ExpressionNode, DataType]


def _is_null_literal(t: Typed) -> bool:
    return isinstance(t[0], LiteralNode) and t[0].value is None


def _lit(value: Any, typ: DataType) -> Typed:
    return LiteralNode(value=value, type=typ), typ


def _null() -> Typed:
    return _lit(None, UNKNOWN)


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
        if _whole(t[1]):  # whole numbers convert to their decimal digits
            return self.call("to_string", t)
        if t[1].kind is TypeKind.DECIMAL:
            self.notes.add("pc.expr.number-text")
            return self.call("to_string", t)
        raise Opaque(f"{t[1].kind.value} value used as a string (implicit conversion)")

    def as_ts(self, t: Typed, fn: str) -> Typed:
        if t[1].kind in (TypeKind.TIMESTAMP, TypeKind.DATE) or _is_null_literal(t):
            return t
        raise Opaque(f"{fn} expects a date, got {t[1].kind.value}")

    def str_literal(self, a: Ast, what: str) -> str | None:
        t = self.conv(a)
        if _is_null_literal(t):
            return None
        if (
            isinstance(t[0], LiteralNode)
            and isinstance(t[0].value, str)
            and t[1].kind is TypeKind.STRING
        ):
            return t[0].value
        raise Opaque(f"{what} must be a string literal")

    def int_literal(self, a: Ast, what: str) -> int:
        t = self.conv(a)
        value = t[0].value if isinstance(t[0], LiteralNode) else None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int) and t[1].kind in INTEGRAL:
            return value
        raise Opaque(f"{what} must be an integer literal")

    def date_format(self, a: Ast | None, fn: str) -> str:
        if a is None:
            self.notes.add("pc.expr.default-date-format")
            return DEFAULT_DATE_FORMAT
        fmt = (self.str_literal(a, f"{fn} format") or "").upper()
        tokens = parse_format(fmt)
        if tokens is None or (fn != "TO_CHAR" and "YY" in tokens):
            raise Opaque(f"{fn} format {fmt!r} is not supported")
        return fmt

    def date_unit(self, a: Ast, fn: str) -> Typed:
        fmt = self.str_literal(a, f"{fn} format")
        unit = _DATE_UNITS.get((fmt or "").upper())
        if unit is None:
            raise Opaque(f"{fn} format {fmt!r} is not supported")
        return _lit(unit, STRING)

    def branches(self, values: list[Typed], fn: str) -> list[Typed]:
        """Result values of IIF/DECODE: booleans mix with numbers as 1/0; NULL literals and
        fail() calls take the other branches' type; otherwise the types must agree."""
        kinds = {v[1].kind for v in values}
        if TypeKind.BOOLEAN in kinds and kinds & set(NUMERIC):
            values = [self.as_num(v) for v in values]
        known = [v[1] for v in values if v[1].kind is not TypeKind.UNKNOWN]
        if known and common_type(known).kind is TypeKind.UNKNOWN:
            shown = ", ".join(sorted({t.kind.value for t in known}))
            raise Opaque(f"{fn} results differ ({shown})")
        return values

    @staticmethod
    def default_for(t: Typed) -> Typed:
        """IIF without an else value returns 0, '' or NULL depending on the then-value type."""
        if t[1].kind in NUMERIC:
            return _lit(0, INT)
        if t[1].kind is TypeKind.STRING:
            return _lit("", STRING)
        return _null()

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
                self.notes.add("pc.expr.mapping-variable")
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
        if tag == "builtin":
            return self.builtin(a[1])
        if tag == "lkp":
            if self.env.lookup_call is None or self.in_agg:
                raise Opaque("unconnected lookup call is not supported here")
            return self.env.lookup_call(a[1], [self.conv(x) for x in a[2]])
        raise Opaque(f"unsupported syntax {tag}")

    def builtin(self, name: str) -> Typed:
        if name in self.env.builtins:
            if name == "SYSDATE":
                self.notes.add("pc.expr.sysdate")
            return self.env.builtins[name]
        if re.fullmatch(r"\$PM[A-Z]+", name) and self.env.pm_parameter is not None:
            return self.env.pm_parameter(name)
        if name.startswith("$"):
            raise Opaque(f"built-in variable {name} is not supported")
        raise Opaque(f"built-in {name} depends on the run time and is not supported")

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
        handler = getattr(self, f"fn_{name.lower()}", None)
        if handler is not None and re.fullmatch(r"[A-Z_]+", name):
            result: Typed | None = handler(args)
            if result is not None:
                return result
        if name in _STRING_FNS and n == 1:
            return self.call(_STRING_FNS[name], self.as_str(self.conv(args[0])))
        if name in _AGGS or name == "COUNT*":
            return self.aggregate(name, args)
        raise Opaque(f"function {name}/{n} is not in the supported subset")

    # Each fn_<NAME> returns None when the call shape is outside the supported subset.

    def fn_iif(self, args: list[Ast]) -> Typed | None:
        if len(args) not in (2, 3):
            return None
        cond = self.as_bool(self.conv(args[0]))
        then = self.conv(args[1])
        other = self.conv(args[2]) if len(args) == 3 else self.default_for(then)
        then, other = self.branches([then, other], "IIF")
        return self.call("if", cond, then, other)

    def fn_decode(self, args: list[Ast]) -> Typed | None:
        if len(args) < 3:
            return None
        rest = list(args[1:])
        default = self.conv(rest.pop()) if len(rest) % 2 else _null()
        conditional = args[0] == ("bool", True)  # DECODE(TRUE, cond1, value1, ...)
        value = None if conditional else self.conv(args[0])
        conds: list[Typed] = []
        results: list[Typed] = []
        for search_ast, result_ast in zip(rest[0::2], rest[1::2], strict=True):
            if value is None:
                conds.append(self.as_bool(self.conv(search_ast)))
            else:
                search = self.conv(search_ast)
                if _is_null_literal(search):
                    raise Opaque("DECODE with a NULL search value (NULL matching is unverified)")
                conds.append(self.call("eq", *self.comparable(value, search)))
            results.append(self.conv(result_ast))
        *results, default = self.branches([*results, default], "DECODE")
        flat = [x for pair in zip(conds, results, strict=True) for x in pair]
        return self.call("case", *flat, default)

    def fn_in(self, args: list[Ast]) -> Typed | None:
        if len(args) < 2:
            return None
        value = self.conv(args[0])
        items = [self.conv(x) for x in args[1:]]
        ignore_case = False
        last = items[-1]
        if (
            value[1].kind is TypeKind.STRING
            and len(items) >= 2
            and isinstance(last[0], LiteralNode)
            and last[1].kind in INTEGRAL
        ):
            ignore_case = last[0].value == 0
            items = items[:-1]
        elif value[1].kind is TypeKind.STRING and any(
            not isinstance(i[0], LiteralNode) or re.search(r"[A-Za-z]", str(i[0].value))
            for i in items
        ):
            raise Opaque("IN on letters without a CaseFlag (default case sensitivity unverified)")
        terms: list[Typed] = []
        for item in items:
            if _is_null_literal(item):
                raise Opaque("IN list containing NULL")
            lt, rt = self.comparable(value, item)
            if ignore_case:
                lt, rt = self.call("upper", lt), self.call("upper", rt)
            terms.append(self.call("eq", lt, rt))
        out = terms[0]
        for t in terms[1:]:
            out = self.call("or", out, t)
        return out

    def fn_isnull(self, args: list[Ast]) -> Typed | None:
        return self.call("is_null", self.conv(args[0])) if len(args) == 1 else None

    def fn_length(self, args: list[Ast]) -> Typed | None:
        return self.call("length", self.as_str(self.conv(args[0]))) if len(args) == 1 else None

    def fn_concat(self, args: list[Ast]) -> Typed | None:
        if len(args) != 2:
            return None
        return self.call("concat", *(self.as_str(self.conv(x)) for x in args))

    def fn_substr(self, args: list[Ast]) -> Typed | None:
        if len(args) not in (2, 3):
            return None
        s = self.as_str(self.conv(args[0]))
        nums = [self.as_num(self.conv(x)) for x in args[1:]]
        for t in nums:
            if t[1].kind not in (*INTEGRAL, TypeKind.UNKNOWN):
                raise Opaque("SUBSTR position/length must be integers")
        return self.call("substr", s, *nums)

    def fn_abs(self, args: list[Ast]) -> Typed | None:
        return self.call("abs", self.as_num(self.conv(args[0]))) if len(args) == 1 else None

    def fn_sign(self, args: list[Ast]) -> Typed | None:
        return self.call("sign", self.as_num(self.conv(args[0]))) if len(args) == 1 else None

    def _pad(self, fn: str, args: list[Ast]) -> Typed | None:
        if len(args) not in (2, 3):
            return None
        s = self.as_str(self.conv(args[0]))
        length = self.as_num(self.conv(args[1]))
        if length[1].kind not in (*INTEGRAL, TypeKind.UNKNOWN):
            raise Opaque(f"{fn.upper()} length must be an integer")
        fill = self.str_literal(args[2], f"{fn.upper()} pad string") if len(args) == 3 else " "
        if not fill:
            raise Opaque(f"{fn.upper()} with an empty or NULL pad string")
        return self.call(fn, s, length, _lit(fill, STRING))

    def fn_lpad(self, args: list[Ast]) -> Typed | None:
        return self._pad("lpad", args)

    def fn_rpad(self, args: list[Ast]) -> Typed | None:
        return self._pad("rpad", args)

    def fn_instr(self, args: list[Ast]) -> Typed | None:
        if not 2 <= len(args) <= 4:
            return None
        s, search = self.as_str(self.conv(args[0])), self.as_str(self.conv(args[1]))
        start = self.int_literal(args[2], "INSTR start") if len(args) > 2 else 1
        if start < 1:
            raise Opaque("INSTR with a start position below 1 (backward search)")
        if len(args) == 4 and self.int_literal(args[3], "INSTR occurrence") != 1:
            raise Opaque("INSTR occurrence other than 1")
        return self.call("instr", s, search, _lit(start, INT))

    def fn_replacechr(self, args: list[Ast]) -> Typed | None:
        if len(args) != 4:
            return None
        case_sensitive = self.int_literal(args[0], "REPLACECHR CaseFlag") != 0
        s = self.as_str(self.conv(args[1]))
        old = self.str_literal(args[2], "REPLACECHR OldCharSet")
        new = self.str_literal(args[3], "REPLACECHR NewChar") or ""
        if not old:
            return s
        chars: list[str] = []
        for c in old:
            for variant in (c,) if case_sensitive else (c, c.lower(), c.upper()):
                if variant not in chars:
                    chars.append(variant)
        to = new[:1] * len(chars)
        return self.call("translate", s, _lit("".join(chars), STRING), _lit(to, STRING))

    def fn_replacestr(self, args: list[Ast]) -> Typed | None:
        if len(args) < 4:
            return None
        if len(args) > 4:
            raise Opaque("REPLACESTR with several search strings")
        case_sensitive = self.int_literal(args[0], "REPLACESTR CaseFlag") != 0
        s = self.as_str(self.conv(args[1]))
        old = self.str_literal(args[2], "REPLACESTR OldString")
        new = self.str_literal(args[3], "REPLACESTR NewString") or ""
        if not old:
            return s
        fn = "replace" if case_sensitive else "replace_ci"
        return self.call(fn, s, _lit(old, STRING), _lit(new, STRING))

    def fn_chr(self, args: list[Ast]) -> Typed | None:
        return self.call("chr", self.as_num(self.conv(args[0]))) if len(args) == 1 else None

    def fn_is_number(self, args: list[Ast]) -> Typed | None:
        if len(args) != 1:
            return None
        return self.call("matches_number", self.as_str(self.conv(args[0])))

    def fn_is_spaces(self, args: list[Ast]) -> Typed | None:
        if len(args) != 1:
            return None
        return self.call("is_whitespace", self.as_str(self.conv(args[0])))

    def fn_is_date(self, args: list[Ast]) -> Typed | None:
        if len(args) not in (1, 2):
            return None
        s = self.as_str(self.conv(args[0]))
        fmt = self.date_format(args[1] if len(args) == 2 else None, "IS_DATE")
        return self.call("can_parse_timestamp", s, _lit(fmt, STRING))

    def fn_to_date(self, args: list[Ast]) -> Typed | None:
        if len(args) not in (1, 2):
            return None
        value = self.conv(args[0])
        if value[1].kind in (TypeKind.TIMESTAMP, TypeKind.DATE):
            return value
        s = self.as_str(value)
        fmt = self.date_format(args[1] if len(args) == 2 else None, "TO_DATE")
        self.notes.add("pc.expr.to-date")
        return self.call("parse_timestamp", s, _lit(fmt, STRING))

    def fn_to_char(self, args: list[Ast]) -> Typed | None:
        if len(args) not in (1, 2):
            return None
        value = self.conv(args[0])
        kind = value[1].kind
        if kind in (TypeKind.TIMESTAMP, TypeKind.DATE):
            fmt = self.date_format(args[1] if len(args) == 2 else None, "TO_CHAR")
            return self.call("format_timestamp", value, _lit(fmt, STRING))
        if len(args) == 2:
            raise Opaque(f"TO_CHAR with a format on a {kind.value} value")
        if kind is TypeKind.STRING or _is_null_literal(value):
            return value
        if _whole(value[1]):
            return self.call("to_string", value)
        if kind is TypeKind.DECIMAL:
            self.notes.add("pc.expr.number-text")
            return self.call("to_string", value)
        raise Opaque(f"TO_CHAR of a {kind.value} value (number formatting unverified)")

    def fn_to_decimal(self, args: list[Ast]) -> Typed | None:
        if len(args) not in (1, 2):
            return None
        if len(args) == 2:
            scale = self.int_literal(args[1], "TO_DECIMAL scale")
        else:
            scale = 18
            self.notes.add("pc.expr.to-decimal-scale")
        if not 0 <= scale <= 18:
            raise Opaque("TO_DECIMAL scale outside 0..18")
        target = _T(kind=TypeKind.DECIMAL, precision=38, scale=scale)
        value = self.conv(args[0])
        if value[1].kind is TypeKind.STRING:
            self.notes.add("pc.expr.to-number")
            number = self.call("leading_decimal", value, _lit(scale, INT))
        elif value[1].kind in NUMERIC:
            number = self.call("round", value, _lit(scale, INT))
        elif _is_null_literal(value):
            return _lit(None, target)
        else:
            raise Opaque(f"TO_DECIMAL of a {value[1].kind.value} value")
        return CastNode(to=target, arg=number[0]), target

    def fn_to_integer(self, args: list[Ast]) -> Typed | None:
        if len(args) not in (1, 2):
            return None
        truncate = len(args) == 2 and self.int_literal(args[1], "TO_INTEGER flag") != 0
        value = self.conv(args[0])
        if value[1].kind is TypeKind.STRING:
            self.notes.add("pc.expr.to-number")
            value = self.call("leading_decimal", value, _lit(18, INT))
        elif _is_null_literal(value):
            return _lit(None, INT)
        elif value[1].kind not in NUMERIC:
            raise Opaque(f"TO_INTEGER of a {value[1].kind.value} value")
        if value[1].kind in INTEGRAL:
            return CastNode(to=INT, arg=value[0]), INT
        if truncate:
            value = self.call("trunc", value, _lit(0, INT))
        return CastNode(to=INT, arg=value[0]), INT  # canonical casts round half away from 0

    def fn_trunc(self, args: list[Ast]) -> Typed | None:
        if len(args) not in (1, 2):
            return None
        value = self.conv(args[0])
        if value[1].kind in (TypeKind.TIMESTAMP, TypeKind.DATE):
            unit = self.date_unit(args[1], "TRUNC") if len(args) == 2 else _lit("day", STRING)
            return self.call("trunc_timestamp", value, unit)
        value = self.as_num(value)
        places = self.int_literal(args[1], "TRUNC precision") if len(args) == 2 else 0
        if places < 0:
            raise Opaque("TRUNC with a negative precision")
        return self.call("trunc", value, _lit(places, INT))

    def fn_round(self, args: list[Ast]) -> Typed | None:
        if len(args) not in (1, 2):
            return None
        value = self.conv(args[0])
        if value[1].kind in (TypeKind.TIMESTAMP, TypeKind.DATE):
            raise Opaque("ROUND of a date")
        value = self.as_num(value)
        places = self.int_literal(args[1], "ROUND precision") if len(args) == 2 else 0
        if places < 0:
            raise Opaque("ROUND with a negative precision")
        return self.call("round", value, _lit(places, INT))

    def fn_add_to_date(self, args: list[Ast]) -> Typed | None:
        if len(args) != 3:
            return None
        date = self.as_ts(self.conv(args[0]), "ADD_TO_DATE")
        unit = self.date_unit(args[1], "ADD_TO_DATE")
        amount = self.as_num(self.conv(args[2]))
        if amount[1].kind not in (*INTEGRAL, TypeKind.UNKNOWN):
            raise Opaque("ADD_TO_DATE with a fractional amount")
        return self.call("add_interval", date, unit, amount)

    def fn_get_date_part(self, args: list[Ast]) -> Typed | None:
        if len(args) != 2:
            return None
        date = self.as_ts(self.conv(args[0]), "GET_DATE_PART")
        return self.call("timestamp_part", date, self.date_unit(args[1], "GET_DATE_PART"))

    def fn_abort(self, args: list[Ast]) -> Typed | None:
        if len(args) != 1:
            return None
        text = self.conv(args[0])
        if isinstance(text[0], LiteralNode) and isinstance(text[0].value, str):
            message = text[0].value
        else:
            self.notes.add("pc.expr.abort-message")
            message = "ABORT (message computed at run time in the source)"
        return self.call("fail", _lit(message, STRING))

    def fn_setvariable(self, args: list[Ast]) -> Typed | None:
        if len(args) != 2:
            return None
        if args[0][0] != "param":
            raise Opaque("SETVARIABLE target must be a mapping variable")
        self.conv(args[0])  # must be declared
        value = self.conv(args[1])
        if _is_null_literal(value):
            raise Opaque("SETVARIABLE with NULL returns the variable's current value (stateful)")
        self.notes.add("pc.expr.setvariable")
        return value

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
