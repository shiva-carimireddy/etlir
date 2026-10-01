# Canonical IR semantics (0.1.0)

This page defines what Canonical IR constructs **mean**. Source adapters map native
constructs onto these semantics (or mark them unsupported/opaque); target emitters must
implement them exactly. Where a source platform's behavior is believed to differ, the
adapter records it as an approximation in the evidence ledger. It is never silently
accepted.

## Relations and operations

An operation produces one relation per output group. **Every output relation contains
exactly the declared output columns, cast to their declared types.** Columns reach an
operation through edges into an input slot. An edge's column list renames upstream columns
into the operation's input columns; an empty list passes all upstream columns by name. All
edges into one slot come from the same upstream output group (IR-V-013).

| Operation | Semantics |
|---|---|
| `read` | Rows of the dataset; output columns are the dataset's columns. |
| `project` | Declared output columns taken from the input by name. |
| `derive` | Per row, each assignment is computed from the *same* input row; unassigned output columns pass through. |
| `filter` | Keeps rows whose predicate is TRUE. NULL is not TRUE. |
| `route` | A row goes to **every** group whose predicate is TRUE; rows matching none go to the default group if declared, else are dropped. |
| `join` | Slots `left`/`right` with disjoint column names; `inner`/`left`/`right`/`full`; NULL keys never match. |
| `aggregate` | Groups by the key columns (NULL keys form one group); aggregation expressions may use aggregate functions and group keys only. With **no** key columns it is a global aggregate: exactly one output row, even for empty input. With no aggregations it is DISTINCT over the keys. |
| `lookup` | For each row of slot `in`, the rows of slot `lookup` where the condition is TRUE (NULL never matches). Input columns pass through; `returns` maps lookup columns to output columns, NULL when nothing matches. `any`: the first match when lookup columns are sorted ascending in declared order, NULLs last (deterministic); `error`: the task fails if any input row has more than one match; `all`: one output row per match. The two slots' column names are disjoint. |
| `sequence` | Passes the input through and adds a bigint column: the rows, ordered by **all** their columns ascending (NULLs first), receive `start`, `start + increment`, … where `start` is a parameter. Rows equal in every column are interchangeable, so the result is deterministic. With slot `after` connected, numbering continues after `count(after)` values (consecutive blocks for two consumers of one generator). |
| `write` | Writes the input to the dataset; dataset columns without an input column are NULL. Modes: `append`, `overwrite`, `error_if_exists`, and the keyed modes `update` and `upsert` (below). |
| `unsupported` | A recognized source construct with no semantics yet. Always blocks. |

**Keyed writes.** `update` and `upsert` match input rows to the dataset's existing rows on
the write's `keys` (plain equality, so NULL keys never match). Matched rows take the input's
values for the columns the input provides; columns it does not provide keep their values.
`update` discards input rows without a match; `upsert` inserts them (unprovided columns
NULL). Duplicate keys in the input fail the task (the order in which several updates would
apply is not defined). A dataset that does not exist yet has no rows.

Row order is not part of the semantics of any operation: `sequence` defines its own order.

## Types and casts

Types: `string`, `integer` (32-bit), `bigint`, `decimal(p,s)` (exact), `double`,
`boolean`, `date`, `timestamp` (UTC, microseconds), `binary`, `unknown`.

`cast` is defined between numeric types (casts to integral types round half away from
zero), from boolean to numeric (TRUE→1, FALSE→0), and from string to decimal (the string
must be a decimal literal; anything else fails the task). The last is used only for
parameter values substituted into SQL text. No implicit string↔number or string↔time
conversion exists; an adapter must emit an opaque expression instead.

String length (`DataType.length`) is informational in 0.1: targets do not truncate or
reject longer values.

## Expressions and functions

Expression nodes: `literal`, `column`, `parameter`, `call`, `cast`, `opaque`. Every
`call` names an entry of the function catalog (`etlir/canonical/functions.py`):

| Function | Semantics |
|---|---|
| `add` `subtract` `multiply` `negate` `abs` | NULL if any argument is NULL. |
| `divide` | NULL if any argument is NULL **or the divisor is zero**. |
| `eq` `ne` `lt` `le` `gt` `ge` | NULL if any argument is NULL. Strings compare exactly and case-sensitively. |
| `and` `or` `not` | SQL three-valued logic. |
| `is_null` | Never NULL. |
| `if(c, a, b)` | `a` when `c` is TRUE, otherwise `b` (a NULL condition selects `b`). |
| `case(c1, v1, …, cn, vn, d)` | Value of the first TRUE condition, else `d` (NULL conditions are not TRUE). An odd number of at least 3 arguments. |
| `coalesce` | First non-NULL argument. |
| `concat(a, b, …)` | NULL arguments count as `''`; the result is NULL only if **every** argument is NULL. |
| `upper` `lower` | NULL-strict. |
| `ltrim` `rtrim` | NULL-strict; remove leading/trailing **spaces** only. |
| `length` | NULL-strict; counts characters. |
| `substr(s, start[, len])` | NULL-strict; 1-based; `start = 0` means 1; negative `start` counts from the end; a start before the first character is clamped to 1; missing `len` means to the end; `len ≤ 0` yields `''`. |
| `sign(x)` | NULL-strict; -1, 0 or 1 (integer). |
| `trunc(x, p)` / `round(x, p)` | NULL-strict; to `p ≥ 0` decimal places, toward zero / half away from zero; result has the type of `x`. |
| `lpad(s, n, pad)` / `rpad(s, n, pad)` | NULL-strict; pad to `n` characters by repeating `pad` on the left/right; a longer `s` is cut to its first `n` characters; `n ≤ 0` yields `''`. |
| `instr(s, sub, start)` | NULL-strict; 1-based position of the first occurrence of `sub` at or after `start ≥ 1`; 0 if none **or if `sub` is `''`**. |
| `translate(s, from, to)` | NULL if `s` is NULL; each character of `from` becomes the character at the same position of `to`, or is removed when `to` is shorter. |
| `replace(s, old, new)` / `replace_ci(…)` | NULL if `s` is NULL; every non-overlapping occurrence of `old`, left to right, case-sensitively / case-insensitively. |
| `chr(n)` | NULL-strict; the ASCII character with code 1–127; NULL for any other code. |
| `matches_number(s)` | NULL-strict; TRUE if `s` is optional spaces, optional sign, digits with an optional fraction (or a fraction alone), an optional exponent, optional spaces. |
| `is_whitespace(s)` | NULL-strict; TRUE if `s` is non-empty and only space, tab, newline, carriage return, form feed or vertical tab. |
| `leading_decimal(s, p)` | NULL if `s` is NULL; the value of the longest numeric prefix after leading spaces (sign, digits, optional fraction), **0 if there is none**, rounded half away from zero to `p` places. |
| `to_string(x)` | NULL-strict; plain decimal notation: integers as digits, decimals without trailing fractional zeros or a trailing point (`12.50` → `12.5`, `3.00` → `3`). |
| `format_timestamp(t, f)` | NULL-strict; `t` formatted with `f`. |
| `parse_timestamp(s, f)` | NULL if `s` is NULL; `s` must match `f` exactly (every field at its full width, a valid calendar date and time), **otherwise the task fails**. |
| `can_parse_timestamp(s, f)` | NULL-strict; TRUE if `parse_timestamp(s, f)` would succeed. |
| `trunc_timestamp(t, unit)` | NULL-strict; truncates to the start of the `year`, `month`, `day`, `hour` or `minute`. |
| `add_interval(t, unit, n)` | NULL-strict; adds `n` units (`year` … `second`); month and year arithmetic keeps the day, clamped to the last day of the month. |
| `timestamp_part(t, unit)` | NULL-strict; the `year`, `month`, `day`, `hour`, `minute` or `second` (integer). |
| `fail(message)` | Fails the task with the message when evaluated (only rows that evaluate it). |
| `sum` `min` `max` `avg` | Ignore NULLs; NULL when there is no non-NULL input. |
| `count(x)` / `count_all()` | Non-NULL values / rows. |

Arguments that fix the meaning of a call must be literals (invariant IR-V-014): the pad
string of `lpad`/`rpad`, the start of `instr`, the character sets of `translate`, the
strings of `replace`/`replace_ci`, the scale of `leading_decimal`, the places of
`trunc`/`round`, formats and units, and the `fail` message.

**Timestamp formats** are built from the tokens `YYYY` (4 digits), `MM`, `DD`, `HH24`, `MI`,
`SS` (2 digits each), `YY` (last two digits of the year; formatting only, since parsing it
would need a century) and the separators `- / : . T` and space, for example `YYYY-MM-DD`,
`MM/DD/YYYY HH24:MI:SS` or `YYYYMMDD`. Nothing else is part of the canonical format language.

**Built-in parameters.** A parameter with `builtin = "run_start_time"` has the run's start
instant (UTC) as its value when the run does not set it; every task of a run sees the same
instant (the reference runner sets it once per run).

`opaque` holds unparsed source text with a reason. Its only capability is `blocked`.

## Workflow

A pipeline's tasks form a DAG. A dependency has a condition: `success`, `failure`,
`completion` (either), or `expression` (opaque in 0.1). A task runs when **all** its
dependency conditions hold (`trigger = all`); `trigger = any` is representable but no 0.1
target supports it. A task whose conditions cannot hold does not run, and neither do its
dependents that require it to succeed.

## Dataset bindings (runtime)

Physical locations are not part of the Canonical IR. A bindings file maps each binding id
to `{format, path, options}`. For `csv` input: header row by default, `,` delimiter,
`"` quote; **an empty field, quoted or not, is NULL** (Spark's CSV reader cannot tell the
two apart, so both targets use this rule); timestamps `yyyy-MM-dd HH:mm:ss`; malformed
records fail the task. Output is `jsonl` (one JSON object per line, NULLs kept, decimals as
exact strings) in a directory of `part-*.json` files.

## Where engines differ, and what emitters do about it

Found by probing both engines and pinned by the `pc-expression-semantics` case:

| Behavior | Spark 4.2 | DuckDB 1.5 | Canonical rule and how emitters enforce it |
|---|---|---|---|
| Decimal → integer cast | truncates | rounds | round half away from zero; both emit an explicit `round` |
| `substring(s, 0, n)` | as position 1 | before position 1 | position normalized in generated code before calling the engine |
| Quoted empty CSV field | NULL | `''` by default | NULL; DuckDB reads with `allow_quoted_nulls=true` |
| Division by zero | error (ANSI mode) | `inf` | NULL; `try_divide` / `CASE WHEN divisor = 0` |
| "Any" match in a lookup | no built-in | no built-in | explicit ordered pick (`row_number` over the declared lookup columns) |
| Failing on duplicate lookup matches | no built-in | no built-in | explicit check that raises (`count` / DuckDB `error()`) |
| Decimal division result | decimal | double | result cast to the declared column type |
| `instr`/`locate` with an empty needle | 1 at any position | position of the start | 0; explicit guard |
| `chr` above 127 | code mod 256 | Unicode code point | NULL outside 1–127; explicit guard |
| `signum` result type | double | tinyint | cast to integer |
| Parsing `MM` from `1` | rejected | accepted | full-width fields only: a strict regular expression is checked before parsing |
| Parsing an invalid string | error or NULL by function | error or NULL by function | the `try_` form plus an explicit `raise_error` / `error()` |
