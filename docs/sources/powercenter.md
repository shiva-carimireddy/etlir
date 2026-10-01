# Source adapter: Informatica PowerCenter XML (`powercenter-xml`)

Reads repository exports (`<POWERMART>` XML, as produced by the PowerCenter Repository
Manager or Designer/Workflow Manager export). Detection is content-based because exports
often have no file extension. Implemented from public documentation and inspection of
public exports; not affiliated with or endorsed by Informatica.

## Loading (security)

`defusedxml` with DTD loading disabled, entity declarations rejected (no expansion
attacks), external references rejected (no XXE, no network), and a size cap (256 MiB by
default). The standard `<!DOCTYPE POWERMART SYSTEM "powrmart.dtd">` line is accepted but
never resolved. Rejections: `PC-X-001` malformed, `PC-X-002` forbidden construct,
`PC-X-003` too large, `PC-X-004` root is not `POWERMART`.

## Raw IR

Every element becomes `{tag, attrs, children[, text]}` with exact attribute values and
order of children, including unknown elements. **Preservation test:** rebuilding XML from
the Raw IR yields an element tree equal to the parsed source (tags, attributes, child
order, non-whitespace text). It passes for the fixtures and for all 67 pinned public
exports. Documented omissions: comments, processing instructions, whitespace-only text,
attribute order, lexical form of character references. The XML declaration's encoding and
the DOCTYPE are recorded.

## Resolution

All files given together form one corpus group. A repository-level symbol table keyed by
repository/folder resolves session → mapping, task instance → task/session, and
mapping instance → source/target/transformation/mapplet **across files**. Identical
duplicate definitions merge; differing definitions with one name are conflicts
(`PC-R-002`) and nothing resolves to them. A missing definition (`PC-R-001`) makes the
referring task unsupported with evidence status `MISSING_INFORMATION`; a missing companion
export is never guessed from a name. Mapping instances are keyed by name **and**
transformation type (a source and its source qualifier may share a name).

## Normalization rules (`pc.*`)

| PowerCenter construct | Canonical construct | Conditions (otherwise: unsupported with reason) |
|---|---|---|
| SOURCE / TARGET | Dataset + Binding | COBOL group items are flattened; VSAM/IMS reads are unsupported |
| Session + mapping | Dataflow (one per session; mapping-only dataflow if no session) | |
| Source Definition instance | `read` | |
| Source Qualifier | `project` | without SQL attributes |
| Source Qualifier with SQL query, source filter, user-defined join or select distinct (mapping or session value) | reads → `join`s → `filter` → `derive` or `aggregate` → optional distinct | the SQL is in the accepted subset (see [SQL overrides](#sql-overrides)); pre/post SQL stays blocked (side effects); multi-source qualifiers need a user-defined join or a query |
| Lookup (connected) | `lookup` with a lookup-source subgraph | static cache; condition in the expression subset (equality or range); no no-match default values; SQL override / source filter in the accepted subset; policy mapping below |
| Lookup (unconnected, `:LKP.name(args)` in an Expression) | argument `derive` → `lookup` per distinct call → the expression reads the returned column | same as connected; policy is not "return all" |
| Several upstream transformations into one (row-aligned merge) | fused into one row stream ([row alignment](#row-aligned-merges)) | all branches descend from the same relation through row-preserving steps |
| Expression | `derive` | stateless variable ports are inlined in port order; a variable read before it is set (previous-row state) makes dependent outputs opaque; no input default values; no custom output error defaults |
| Filter | `filter` | numeric conditions become `<> 0`; empty condition means TRUE |
| Router | `route` | output ports must reference input ports (`REF_FIELD`); a row reaches every matching group |
| Joiner | `join` | detail = `left`, master = `right`; Normal→inner, Master Outer→left, Detail Outer→right, Full Outer→full; case-sensitive comparison |
| Aggregator | `aggregate` | at least one group-by port; no pass-through non-key ports (last-row values); no variable ports |
| Target Definition instance | `write` | session "Treat source rows as" = Insert; mode from the session writer (Append if Exists / truncate), `append` with `PC-N-003` when there is no session |
| Mapping parameter `$$X` (ISPARAM=YES) | Parameter (dataflow scope) | |
| Mapping variable `$$X` (ISPARAM=NO) | Parameter (value supplied at run time; no default) | the mapping does not change it with `SETVARIABLE`/`SETMAXVARIABLE`/…; otherwise it carries state and is opaque |
| WORKFLOW | Pipeline | worklets are not expanded |
| TASKINSTANCE | Task | Session→dataflow; Command, Email, Event Wait, Timer, Decision, Assignment keep their kind; disabled tasks are unsupported |
| WORKFLOWLINK | Dependency | empty condition → `completion`; `$T.Status = SUCCEEDED/FAILED` for the link's own source task → `success`/`failure`; anything else → opaque `expression` |
| `TREAT_INPUTLINK_AS_AND=NO` | `trigger = any` | |

Also recognized and reported as unsupported in 0.1: Update Strategy, Sequence Generator,
Normalizer, Sorter, Rank, Union, Custom, Stored Procedure, Transaction Control, mapplets,
dynamic lookup caches, and merges that are not row-aligned (in the public corpus every such
merge traces back to one of these unsupported upstreams).

### Update strategies, sequences, global aggregators

* **Update Strategy** (constant `DD_INSERT`/`DD_UPDATE`, or 0/1): rows pass unchanged. A
  session that treats rows as *Insert* inserts them all. A *Data driven* session applies the
  constant at each target: `DD_UPDATE` becomes a keyed `update` on the target's primary key
  (*Update as Update*), an `upsert` (*Update else Insert*) or an insert (*Update as Insert*).
  Row-level expressions, `DD_DELETE`/`DD_REJECT`, flat-file targets, targets without a primary
  key and targets truncated before the load are unsupported.
* **Sequence Generator**: when `NEXTVAL` feeds one transformation that has one other
  upstream, that upstream is routed through a canonical `sequence` op. The start value is a
  run parameter whose default is the export's *Current Value* (*Start Value* with *Reset*).
  `APPROXIMATED`: values are assigned in a deterministic order (all columns ascending), not
  PowerCenter's arrival order, and the final value is not persisted between runs. Cycling
  generators, end values and `CURRVAL` are unsupported. With two consumers, each gets a
  block of consecutive values (as PowerCenter documents); the first block goes to the
  consumer that sorts first by name.
* **Sorter**: rows pass unchanged (relations are unordered); *Distinct* removes duplicates.
* **Aggregator without group-by ports**: a global aggregate that emits no row for empty
  input (aggregate with a row count, then a filter on the count). `APPROXIMATED`: the
  empty-input behavior follows community documentation, not a verified runtime.
* A transformation port that receives nothing and passes nothing on is dropped; a
  transformation with no connected input at all is unsupported.
* A boolean written to a string port becomes `"1"`/`"0"` (PowerCenter booleans are integers).
* `$PM…` variables other than the names above (for example `$PMRepositoryServiceName`) are
  values of the run environment: run parameters without a default. Run statistics such as
  `$PMTarget@numAffectedRows` stay opaque.

### Lookup policies

| PowerCenter "Lookup policy on multiple match" | Canonical | Evidence |
|---|---|---|
| Use Any Value | `any` (deterministic choice) | TRANSFORMED_EQUIVALENT: any choice is a valid "any" |
| Return All Values on Multiple Match | `all` | TRANSFORMED_EQUIVALENT |
| Use First Value / Use Last Value | `error` | APPROXIMATED: the choice depends on the cache order, which ETLIR does not reproduce; runs only when keys are unique, and fails otherwise instead of guessing |
| Report Error / unset | `error` | APPROXIMATED: PowerCenter reports the row; ETLIR fails the task |

Only lookup ports used by the condition or returned are read; others (for example ports an
override does not select) cannot affect the result.

### SQL overrides

Parsed with SQLGlot in the source's dialect (Oracle, SQL Server, Teradata; generic otherwise).
Accepted: `SELECT [DISTINCT]` over the qualifier's associated sources (or a lookup table
with a definition in the folder); comma joins with join predicates in `WHERE`,
`[INNER] JOIN … ON`, `LEFT [OUTER] JOIN … ON`, Oracle `(+)`; `WHERE`; `GROUP BY` columns with
`COUNT`/`COUNT(*)`/`SUM`/`MIN`/`MAX`/`AVG`; `ORDER BY` (ignored). Expressions: columns,
literals, arithmetic, `||`, comparisons, `AND/OR/NOT`, `IS [NOT] NULL`, `[NOT] IN (list)`,
`BETWEEN`, searched `CASE`, `NVL`/`COALESCE`, `UPPER`, `LOWER`, `TRIM`/`LTRIM`/`RTRIM`,
`LENGTH`, `SUBSTR`. Parameters: `$$X` (numeric; or string next to a numeric operand, read as a
number), `'$$X'` (string), `TO_NUMBER('$$X')`. Oracle: `''` is NULL and `||` ignores NULLs.
Rejected with a reason: `SELECT *`, subqueries, set operations, `HAVING`, `FETCH/LIMIT`,
window functions, other functions, cartesian products, `$PM…` variables, Informatica
`{…}` join syntax. Translated SQL runs outside the source database, so evidence is
`APPROXIMATED` (binary collation assumed) and `PC-N-005` is reported.

### Row-aligned merges

A transformation may take ports from several upstream transformations of one pipeline.
ETLIR accepts this when every branch reaches the same upstream relation through
row-preserving steps (Expression, Source Qualifier without SQL, Lookup with a single-row
policy). The branches are then re-expressed as one chain over a widened row
(`etlir.canonical.rowalign`), and `PC-N-006` is reported. Otherwise the transformation is
unsupported with the reason ("not row-aligned", or "through unsupported upstream X").

## Expression language subset

Operator precedence (highest first): `()`, unary `+ - NOT`, `* / %`, `+ -`, `||`,
`< <= > >=`, `= <> != ^=`, `AND`, `OR`. Comments `--` and `//`.

Supported: numeric and string literals, `NULL`, `TRUE`/`FALSE`, port references
(case-insensitive), mapping parameters and variables, arithmetic `+ - * /`, `||`,
comparisons, `AND OR NOT`, unconnected lookup calls `:LKP.name(args)` (in Expression
transformations), and in aggregators `SUM`, `COUNT`, `COUNT(*)`, `MIN`, `MAX`, `AVG`.
Functions:

| PowerCenter | Canonical | Notes |
|---|---|---|
| `IIF(c, a[, b])` | `if` | without `b`: 0 for numbers, `''` for strings, NULL otherwise (documented default) |
| `DECODE(v, s1, r1, …[, d])` | `case` over `eq(v, si)` | no default → NULL; `DECODE(TRUE, c1, r1, …)` uses the conditions directly; a NULL search value is opaque |
| `IN(v, a, b, …[, CaseFlag])` | `or` of `eq` | CaseFlag 0 compares upper-cased; without a CaseFlag, lists with letters are opaque (default unverified); a NULL item is opaque |
| `ISNULL`, `UPPER`, `LOWER`, `LTRIM`/`RTRIM` (1 arg), `LENGTH`, `SUBSTR`, `CONCAT`, `ABS`, `SIGN`, `CHR` | same name | |
| `LPAD`/`RPAD(s, n[, pad])` | `lpad`/`rpad` | pad defaults to a space; must be a non-empty literal |
| `INSTR(s, search[, start[, 1]])` | `instr` | start must be a literal ≥ 1; other occurrences and backward search are opaque |
| `REPLACECHR(flag, s, chars, new)` | `translate` | flag 0 adds both letter cases; NULL/`''` `new` removes |
| `REPLACESTR(flag, s, old, new)` | `replace` / `replace_ci` | one search string only |
| `IS_NUMBER`, `IS_SPACES` | `matches_number`, `is_whitespace` | |
| `TO_DATE(s[, fmt])`, `IS_DATE(s[, fmt])`, `TO_CHAR(date[, fmt])` | `parse_timestamp`, `can_parse_timestamp`, `format_timestamp` | formats limited to `YYYY MM DD HH24 MI SS` (and `YY` for `TO_CHAR`); no format → `MM/DD/YYYY HH24:MI:SS` (`pc.expr.default-date-format`) |
| `TO_CHAR(n)` | `to_string` | whole numbers exactly; fractional decimals as plain notation without trailing zeros (`pc.expr.number-text`: PowerCenter uses at most 15 significant digits); doubles are opaque |
| `TO_DECIMAL(v[, scale])`, `TO_INTEGER(v[, flag])` | `leading_decimal` / `round` / `trunc` + cast | strings convert their leading numeric part, 0 if none (`pc.expr.to-number`) |
| `TRUNC(n[, p])`, `ROUND(n[, p])` | `trunc`, `round` | `p` a literal ≥ 0 |
| `TRUNC(date[, fmt])`, `ADD_TO_DATE`, `GET_DATE_PART` | `trunc_timestamp`, `add_interval`, `timestamp_part` | units Y…/MM/MON/MONTH/D…/HH…/MI/SS |
| `ABORT(msg)` | `fail` | a computed message becomes a fixed one (`pc.expr.abort-message`) |
| `SETVARIABLE($$v, x)` | `x` | see mapping variables below |

Built-ins: `SESSSTARTTIME` and `SYSDATE` read the run-start-time parameter
(`SYSDATE` is `APPROXIMATED`: PowerCenter reads the clock per row); `$PMMappingName`,
`$PMFolderName`, `$PMRepositoryName` and, for session dataflows, `$PMSessionName` become
literals. Other `$PM…` variables stay opaque.

Mapping variables: PowerCenter evaluates references with the variable's start value for the
whole session, so every mapping variable is a run parameter. For a variable changed with
`SETVARIABLE`, reads are `APPROXIMATED` (`pc.expr.mapping-variable`) because ETLIR does not
persist the final value between runs: supply it as the parameter. `SETVARIABLE` returns its
value; with a NULL literal (which returns the current value) it is opaque.

An integer or scale-0 decimal assigned to a string port converts to its digits. An output
port with an empty expression yields NULL (`pc.expr.empty-output`). `TO_DATE` on a string
that does not match fails the run, where PowerCenter rejects the row (`pc.expr.to-date`).

Fail-closed rules (the expression becomes opaque, with a reason):

* `NOT` used directly as an operand of a comparison or arithmetic operator
  (`NOT A = B`): the precedence is ambiguous across references, so it is not guessed.
* Anything not listed above: other functions and call shapes, `%`, stored procedure and
  mapplet calls (`:SP.`, `:MPLT.`), other built-in variables, string→number and fractional
  number→string implicit conversions, type-mismatched comparisons, IIF/DECODE results of
  different types, and variable ports that read their own or a later variable's value
  (stateful: they depend on the previous row).

Known approximation: division by zero yields NULL canonically; PowerCenter's behavior was
not verified against a runtime, so expressions using `/` carry evidence status
`APPROXIMATED`. A session with *Enable high precision* off (the export default) makes
PowerCenter compute decimals as doubles; dataflows using decimals then get `PC-N-002` and
status `APPROXIMATED`.

## Diagnostic codes

| Code | Meaning |
|---|---|
| PC-X-001…004 | Rejected input (see Loading) |
| PC-R-001 | Referenced definition not found in the loaded group |
| PC-R-002 | Conflicting definitions share one name |
| PC-R-003 | Workflow link references an unknown task instance |
| PC-N-001 | Datatype not recognized (typed unknown; reads using it are blocked) |
| PC-N-002 | High precision disabled; decimals approximate |
| PC-N-003 | No session defines the write mode; append assumed |
| PC-N-004 | Schedule information is not translated |
| PC-N-005 | SQL override translated; runs outside the source database |
| PC-N-006 | Row-aligned inputs fused into one row stream |
