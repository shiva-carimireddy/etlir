# DuckDB Target Adapter

The built-in DuckDB adapter translates a deliberately small Pipeline Semantic Model subset into
one SQL script.

## Supported subset

- A single linear operation chain.
- One `read` operation from a CSV file dataset.
- Any number of `filter` and `derive` operations.
- One terminal `write` operation to a Parquet file dataset.
- Field, literal, and supported binary expressions.

Each intermediate operation becomes a named common table expression. The terminal write becomes
a DuckDB `COPY` statement. Declared source fields are rendered as explicit DuckDB CSV column types
rather than relying on type inference.

## Unsupported behavior

The adapter blocks generation for:

- External operations.
- Branching or multi-input graph shapes.
- Multiple writers.
- Non-CSV readers or non-Parquet writers.
- Unsupported expressions or parameters.

It returns one diagnostic for each detected limitation. It does not generate comments or
placeholders that could be mistaken for a complete pipeline.

## Safety

Identifiers and string literals are escaped by the adapter. Dataset paths are rendered into SQL
but are not accessed during translation. Generated SQL should be reviewed before execution.
