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
(case-insensitive), declared mapping parameters, arithmetic `+ - * /`, `||`, comparisons,
`AND OR NOT`, `IIF` (3 arguments), `ISNULL`, `UPPER`, `LOWER`, `LTRIM`/`RTRIM`
(1 argument), `LENGTH`, `SUBSTR`, `CONCAT`, `ABS`, unconnected lookup calls `:LKP.name(args)` (in
Expression transformations), and in aggregators `SUM`, `COUNT`,
`COUNT(*)`, `MIN`, `MAX`, `AVG`.

Fail-closed rules (the expression becomes opaque, with a reason):

* `NOT` used directly as an operand of a comparison or arithmetic operator
  (`NOT A = B`): the precedence is ambiguous across references, so it is not guessed.
* Anything not listed above: other functions (`DECODE`, `TO_CHAR`, `LPAD`, …), `%`,
  stored procedure and mapplet calls (`:SP.`, `:MPLT.`), built-in variables (`$PM…`, `SYSDATE`, `SESSSTARTTIME`),
  implicit string↔number conversions, type-mismatched comparisons, `IIF` without an else.

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
