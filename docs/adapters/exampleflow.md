# ExampleFlow Source Adapter

ExampleFlow v1 is a fictional JSON source format used only to prove ETLIR's adapter boundary. It
is not based on a commercial platform.

## Supported source steps

| ExampleFlow step | Model operation |
|---|---|
| `read` | `read` |
| `filter` | `filter` |
| `derive` | `derive` |
| `write` | `write` |
| `external_http` | `external_operation` |

The adapter preserves the source step's JSON Pointer in `source_refs`. Input step references are
converted into directed model edges. Dataset paths remain logical relative locations; the adapter
does not open data files.

ExampleFlow filter predicates use SQL-style three-valued logic: a null predicate does not retain
the row. Arithmetic with a null operand produces null. These declared semantics make the example's
filter and derive rules directly representable in DuckDB.

`external_http` is modeled deliberately even though the DuckDB adapter cannot translate it. This
separates source representation capability from target capability.

## Format contract

The format is validated against
`src/etlir/adapters/sources/exampleflow.schema.json`. Unknown properties and unknown step types
are rejected rather than ignored.

## Security behavior

The adapter reads exactly one local JSON file. It does not follow URLs, execute expressions, open
declared datasets, or send network requests.
