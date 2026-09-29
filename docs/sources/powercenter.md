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
| Source Qualifier | `project` | no SQL query, source filter, user-defined join, pre/post SQL, select distinct (mapping or session override) |
| Expression | `derive` | stateless variable ports are inlined in port order; a variable read before it is set (previous-row state) makes dependent outputs opaque; no input default values; no custom output error defaults |
| Filter | `filter` | numeric conditions become `<> 0`; empty condition means TRUE |
| Router | `route` | output ports must reference input ports (`REF_FIELD`); a row reaches every matching group |
| Joiner | `join` | detail = `left`, master = `right`; Normal→inner, Master Outer→left, Detail Outer→right, Full Outer→full; case-sensitive comparison |
| Aggregator | `aggregate` | at least one group-by port; no pass-through non-key ports (last-row values); no variable ports |
| Target Definition instance | `write` | session "Treat source rows as" = Insert; mode from the session writer (Append if Exists / truncate), `append` with `PC-N-003` when there is no session |
| Mapping parameter `$$X` (ISPARAM=YES) | Parameter (dataflow scope) | mapping *variables* carry state between runs and are opaque |
| WORKFLOW | Pipeline | worklets are not expanded |
| TASKINSTANCE | Task | Session→dataflow; Command, Email, Event Wait, Timer, Decision, Assignment keep their kind; disabled tasks are unsupported |
| WORKFLOWLINK | Dependency | empty condition → `completion`; `$T.Status = SUCCEEDED/FAILED` for the link's own source task → `success`/`failure`; anything else → opaque `expression` |
| `TREAT_INPUTLINK_AS_AND=NO` | `trigger = any` | |

Also recognized and reported as unsupported in 0.1: Lookup, Update Strategy, Sequence
Generator, Normalizer, Sorter, Rank, Union, Custom, Stored Procedure, Transaction Control,
mapplets, and passive transformations fed by more than one upstream transformation
(row-aligned merges).

## Expression language subset

Operator precedence (highest first): `()`, unary `+ - NOT`, `* / %`, `+ -`, `||`,
`< <= > >=`, `= <> != ^=`, `AND`, `OR`. Comments `--` and `//`.

Supported: numeric and string literals, `NULL`, `TRUE`/`FALSE`, port references
(case-insensitive), declared mapping parameters, arithmetic `+ - * /`, `||`, comparisons,
`AND OR NOT`, `IIF` (3 arguments), `ISNULL`, `UPPER`, `LOWER`, `LTRIM`/`RTRIM`
(1 argument), `LENGTH`, `SUBSTR`, `CONCAT`, `ABS`, and in aggregators `SUM`, `COUNT`,
`COUNT(*)`, `MIN`, `MAX`, `AVG`.

Fail-closed rules (the expression becomes opaque, with a reason):

* `NOT` used directly as an operand of a comparison or arithmetic operator
  (`NOT A = B`): the precedence is ambiguous across references, so it is not guessed.
* Anything not listed above: other functions (`DECODE`, `TO_CHAR`, `LPAD`, …), `%`,
  unconnected lookups (`:LKP.`), built-in variables (`$PM…`, `SYSDATE`, `SESSSTARTTIME`),
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
