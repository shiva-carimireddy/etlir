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
| `write` | Writes the input to the dataset; dataset columns without an input column are NULL. Modes: `append`, `overwrite`, `error_if_exists`. |
| `unsupported` | A recognized source construct with no semantics yet. Always blocks. |

Row order is not part of the semantics of any 0.1 operation.

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
| `coalesce` | First non-NULL argument. |
| `concat(a, b, …)` | NULL arguments count as `''`; the result is NULL only if **every** argument is NULL. |
| `upper` `lower` | NULL-strict. |
| `ltrim` `rtrim` | NULL-strict; remove leading/trailing **spaces** only. |
| `length` | NULL-strict; counts characters. |
| `substr(s, start[, len])` | NULL-strict; 1-based; `start = 0` means 1; negative `start` counts from the end; a start before the first character is clamped to 1; missing `len` means to the end; `len ≤ 0` yields `''`. |
| `sum` `min` `max` `avg` | Ignore NULLs; NULL when there is no non-NULL input. |
| `count(x)` / `count_all()` | Non-NULL values / rows. |

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
