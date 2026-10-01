"""Canonical function catalog.

Every ``CallNode.function`` must name an entry here. Each entry fixes arity, whether the
function aggregates, its NULL behavior, and its result type. Source adapters map native
functions onto these semantics (or emit an opaque expression); target emitters must
implement them exactly. See docs/semantics.md for the full rules.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from etlir.canonical.model import DataType, TypeKind

NUMERIC = (TypeKind.INTEGER, TypeKind.BIGINT, TypeKind.DECIMAL, TypeKind.DOUBLE)


def _t(kind: TypeKind) -> DataType:
    return DataType(kind=kind)


def numeric_result(types: Sequence[DataType]) -> DataType:
    kinds = {t.kind for t in types}
    if TypeKind.UNKNOWN in kinds:
        return _t(TypeKind.UNKNOWN)
    for k in (TypeKind.DOUBLE, TypeKind.DECIMAL, TypeKind.BIGINT):
        if k in kinds:
            return _t(k)
    return _t(TypeKind.INTEGER)


def common_type(types: Sequence[DataType]) -> DataType:
    """Common type of IF branches; NULL literals are typed UNKNOWN and ignored."""
    known = [t for t in types if t.kind is not TypeKind.UNKNOWN]
    if not known:
        return _t(TypeKind.UNKNOWN)
    if all(t.kind in NUMERIC for t in known):
        return numeric_result(known)
    first = known[0].kind
    return _t(first) if all(t.kind is first for t in known) else _t(TypeKind.UNKNOWN)


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    min_args: int
    max_args: int | None
    aggregate: bool
    nulls: str
    result: Callable[[Sequence[DataType]], DataType]
    literal_args: tuple[int, ...] = ()  # argument positions that must be literals


# Timestamp format strings: tokens YYYY MM DD HH24 MI SS, separated by any of "-/ :.T".
# YY (last two digits of the year) is valid for formatting only: parsing it needs a century.
FORMAT_TOKENS = ("YYYY", "YY", "HH24", "MM", "DD", "MI", "SS")
FORMAT_ONLY_TOKENS = ("YY",)
FORMAT_SEPARATORS = "-/ :.T"
UNITS = ("year", "month", "day", "hour", "minute", "second")


def parse_format(fmt: str) -> list[str] | None:
    """Split a canonical timestamp format into tokens and separators, or None if invalid."""
    out: list[str] = []
    i = 0
    while i < len(fmt):
        for tok in FORMAT_TOKENS:
            if fmt.startswith(tok, i):
                out.append(tok)
                i += len(tok)
                break
        else:
            if fmt[i] not in FORMAT_SEPARATORS:
                return None
            out.append(fmt[i])
            i += 1
    return out if any(t in FORMAT_TOKENS for t in out) else None


def _fixed(kind: TypeKind) -> Callable[[Sequence[DataType]], DataType]:
    return lambda _: _t(kind)


def _first(types: Sequence[DataType]) -> DataType:
    return types[0]


def _divide(types: Sequence[DataType]) -> DataType:
    r = numeric_result(types)
    return (
        r
        if r.kind in (TypeKind.DOUBLE, TypeKind.UNKNOWN)
        else _t(
            TypeKind.DOUBLE if r.kind in (TypeKind.INTEGER, TypeKind.BIGINT) else TypeKind.DECIMAL
        )
    )


def _sum(types: Sequence[DataType]) -> DataType:
    k = types[0].kind
    return _t(TypeKind.BIGINT) if k in (TypeKind.INTEGER, TypeKind.BIGINT) else types[0]


def _avg(types: Sequence[DataType]) -> DataType:
    return _t(TypeKind.DECIMAL) if types[0].kind is TypeKind.DECIMAL else _t(TypeKind.DOUBLE)


STRICT = "NULL if any argument is NULL."

CATALOG: dict[str, FunctionSpec] = {
    f.name: f
    for f in [
        FunctionSpec("add", 2, 2, False, STRICT, numeric_result),
        FunctionSpec("subtract", 2, 2, False, STRICT, numeric_result),
        FunctionSpec("multiply", 2, 2, False, STRICT, numeric_result),
        FunctionSpec("divide", 2, 2, False, STRICT + " NULL if the divisor is zero.", _divide),
        FunctionSpec("negate", 1, 1, False, STRICT, _first),
        FunctionSpec("abs", 1, 1, False, STRICT, _first),
        FunctionSpec(
            "concat",
            2,
            None,
            False,
            "NULL arguments count as empty strings; NULL only if every argument is NULL.",
            _fixed(TypeKind.STRING),
        ),
        *[
            FunctionSpec(op, 2, 2, False, STRICT, _fixed(TypeKind.BOOLEAN))
            for op in ("eq", "ne", "lt", "le", "gt", "ge")
        ],
        FunctionSpec("and", 2, 2, False, "SQL three-valued logic.", _fixed(TypeKind.BOOLEAN)),
        FunctionSpec("or", 2, 2, False, "SQL three-valued logic.", _fixed(TypeKind.BOOLEAN)),
        FunctionSpec("not", 1, 1, False, STRICT, _fixed(TypeKind.BOOLEAN)),
        FunctionSpec("is_null", 1, 1, False, "Never NULL.", _fixed(TypeKind.BOOLEAN)),
        FunctionSpec(
            "if",
            3,
            3,
            False,
            "Returns arg 2 when arg 1 is TRUE, otherwise arg 3 (a NULL condition is not TRUE).",
            lambda ts: common_type(ts[1:]),
        ),
        FunctionSpec(
            "case",
            3,
            None,
            False,
            "Arguments are condition/value pairs followed by a default: returns the value of "
            "the first TRUE condition, else the default (a NULL condition is not TRUE).",
            lambda ts: common_type([*ts[1:-1:2], ts[-1]]),
        ),
        FunctionSpec(
            "coalesce", 1, None, False, "First non-NULL argument.", lambda ts: common_type(ts)
        ),
        FunctionSpec("upper", 1, 1, False, STRICT, _fixed(TypeKind.STRING)),
        FunctionSpec("lower", 1, 1, False, STRICT, _fixed(TypeKind.STRING)),
        FunctionSpec(
            "ltrim", 1, 1, False, STRICT + " Removes leading spaces only.", _fixed(TypeKind.STRING)
        ),
        FunctionSpec(
            "rtrim", 1, 1, False, STRICT + " Removes trailing spaces only.", _fixed(TypeKind.STRING)
        ),
        FunctionSpec(
            "length", 1, 1, False, STRICT + " Counts characters.", _fixed(TypeKind.INTEGER)
        ),
        FunctionSpec(
            "substr",
            2,
            3,
            False,
            STRICT + " 1-based start; start 0 means 1; negative start counts from the end; a start"
            " before the first character is clamped to 1. Missing length means to the end;"
            " length <= 0 yields ''.",
            _fixed(TypeKind.STRING),
        ),
        FunctionSpec("sign", 1, 1, False, STRICT + " -1, 0 or 1.", _fixed(TypeKind.INTEGER)),
        FunctionSpec(
            "lpad",
            3,
            3,
            False,
            STRICT + " Pads on the left with the pad string to length n; a longer string is "
            "cut to its first n characters; n <= 0 yields ''.",
            _fixed(TypeKind.STRING),
            (2,),
        ),
        FunctionSpec(
            "rpad",
            3,
            3,
            False,
            STRICT + " As lpad, padding on the right.",
            _fixed(TypeKind.STRING),
            (2,),
        ),
        FunctionSpec(
            "instr",
            3,
            3,
            False,
            STRICT + " 1-based position of the first occurrence at or after start (start >= 1), "
            "0 if none or if the search string is empty.",
            _fixed(TypeKind.INTEGER),
            (2,),
        ),
        FunctionSpec(
            "translate",
            3,
            3,
            False,
            "NULL if the input is NULL. Each character of arg 2 is replaced by the character at "
            "the same position of arg 3, or removed when arg 3 is shorter.",
            _fixed(TypeKind.STRING),
            (1, 2),
        ),
        FunctionSpec(
            "replace",
            3,
            3,
            False,
            "NULL if the input is NULL. Replaces every non-overlapping occurrence of arg 2, "
            "left to right, case-sensitively.",
            _fixed(TypeKind.STRING),
            (1, 2),
        ),
        FunctionSpec(
            "replace_ci",
            3,
            3,
            False,
            "As replace, matching case-insensitively.",
            _fixed(TypeKind.STRING),
            (1, 2),
        ),
        FunctionSpec(
            "chr",
            1,
            1,
            False,
            STRICT + " ASCII character with code 1-127; NULL for other codes.",
            _fixed(TypeKind.STRING),
        ),
        FunctionSpec(
            "matches_number",
            1,
            1,
            False,
            STRICT + " TRUE if the string is a decimal number: optional surrounding spaces, "
            "optional sign, digits with an optional fraction, optional exponent.",
            _fixed(TypeKind.BOOLEAN),
        ),
        FunctionSpec(
            "is_whitespace",
            1,
            1,
            False,
            STRICT + " TRUE if the string is non-empty and has only space, tab, newline, "
            "carriage return, form feed or vertical tab characters.",
            _fixed(TypeKind.BOOLEAN),
        ),
        FunctionSpec(
            "leading_decimal",
            2,
            2,
            False,
            "NULL if the input is NULL. The decimal value of the longest leading numeric "
            "prefix (after leading spaces), 0 if there is none, rounded half away from zero "
            "to arg 2 decimal places.",
            lambda ts: _t(TypeKind.DECIMAL),
            (1,),
        ),
        FunctionSpec(
            "to_string",
            1,
            1,
            False,
            STRICT + " Plain decimal notation of a number: integers as digits, decimals without "
            "trailing fractional zeros (and no trailing point).",
            _fixed(TypeKind.STRING),
        ),
        FunctionSpec(
            "format_timestamp",
            2,
            2,
            False,
            STRICT + " Formats with a canonical format (YYYY YY MM DD HH24 MI SS).",
            _fixed(TypeKind.STRING),
            (1,),
        ),
        FunctionSpec(
            "parse_timestamp",
            2,
            2,
            False,
            STRICT + " Parses exactly per the format; a string that does not match fails the task.",
            _fixed(TypeKind.TIMESTAMP),
            (1,),
        ),
        FunctionSpec(
            "can_parse_timestamp",
            2,
            2,
            False,
            STRICT + " TRUE if parse_timestamp would succeed.",
            _fixed(TypeKind.BOOLEAN),
            (1,),
        ),
        FunctionSpec(
            "trunc",
            2,
            2,
            False,
            STRICT + " Truncates toward zero to arg 2 (>= 0) decimal places.",
            _first,
            (1,),
        ),
        FunctionSpec(
            "round",
            2,
            2,
            False,
            STRICT + " Rounds half away from zero to arg 2 (>= 0) decimal places.",
            _first,
            (1,),
        ),
        FunctionSpec(
            "trunc_timestamp",
            2,
            2,
            False,
            STRICT + " Truncates to the unit (year, month, day, hour, minute).",
            _fixed(TypeKind.TIMESTAMP),
            (1,),
        ),
        FunctionSpec(
            "add_interval",
            3,
            3,
            False,
            STRICT + " Adds n units (year..second); month arithmetic clamps to the "
            "last day of the month.",
            _fixed(TypeKind.TIMESTAMP),
            (1,),
        ),
        FunctionSpec(
            "timestamp_part",
            2,
            2,
            False,
            STRICT + " Year, month, day, hour, minute or second of the timestamp.",
            _fixed(TypeKind.INTEGER),
            (1,),
        ),
        FunctionSpec(
            "fail",
            1,
            1,
            False,
            "Fails the task with the message when evaluated.",
            _fixed(TypeKind.UNKNOWN),
            (0,),
        ),
        FunctionSpec("sum", 1, 1, True, "Ignores NULLs; NULL if no non-NULL input.", _sum),
        FunctionSpec(
            "count", 1, 1, True, "Counts non-NULL values; 0 for no input.", _fixed(TypeKind.BIGINT)
        ),
        FunctionSpec("count_all", 0, 0, True, "Counts rows.", _fixed(TypeKind.BIGINT)),
        FunctionSpec("min", 1, 1, True, "Ignores NULLs; NULL if no non-NULL input.", _first),
        FunctionSpec("max", 1, 1, True, "Ignores NULLs; NULL if no non-NULL input.", _first),
        FunctionSpec("avg", 1, 1, True, "Ignores NULLs; NULL if no non-NULL input.", _avg),
    ]
}


def arity_ok(name: str, n: int) -> bool:
    spec = CATALOG.get(name)
    if spec is None:
        return False
    if name == "case" and n % 2 == 0:
        return False
    return n >= spec.min_args and (spec.max_args is None or n <= spec.max_args)


def result_type(name: str, arg_types: Sequence[DataType]) -> DataType:
    return CATALOG[name].result(arg_types)


def format_regex(tokens: Sequence[str]) -> str:
    """Anchored regex a string must match to parse with the format (fixed-width digits)."""
    width = {"YYYY": 4}
    body = "".join(
        f"[0-9]{{{width.get(t, 2)}}}" if t in FORMAT_TOKENS else (r"\." if t == "." else t)
        for t in tokens
    )
    return f"^{body}$"


NUMBER_REGEX = r"^ *[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)? *$"
LEADING_NUMBER_REGEX = r"^ *([+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+))"
WHITESPACE_REGEX = r"^[ \t\n\r\f\x0B]+$"
