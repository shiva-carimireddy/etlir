# Scope

## Project scope

ETLIR provides four related capabilities:

1. A versioned platform-neutral model for declared data-pipeline semantics.
2. Contracts for adapting source formats and translating to target representations.
3. Validation and operation-level diagnostic accounting.
4. Machine-readable and human-readable migration evidence.

The architecture is intended to grow toward transformations, datasets, parameters, control flow,
error behavior, external operations, dependencies, scheduling context, and lineage. That future
direction does not imply that version 0.1 already represents those areas completely.

## v0.1 supported surface

Version 0.1 supports:

- A directed acyclic graph of operations.
- File datasets with declared formats and optional fields.
- `read`, `filter`, `derive`, `write`, and `external_operation` model operations.
- Field references, literals, and binary expressions.
- Source references from each model operation to a JSON Pointer in the source artifact.
- Target dispositions: `preserved`, `approximated`, `unsupported`, `ambiguous`, `manual`, and
  `blocked`.
- One synthetic source adapter and one DuckDB SQL target adapter.

The built-in DuckDB adapter supports a narrower subset than the model: a single linear flow using
CSV reads, filters, derived columns, and one Parquet write.

## Non-goals

ETLIR does not:

- Execute or schedule production workloads.
- Discover business intent that the input does not express.
- Guarantee data-level or operational equivalence.
- Replace source and target platform testing.
- Store credentials or resolve live connections.
- Optimize target performance.
- Provide production adapters for proprietary platforms in v0.1.
- Claim migration completeness based only on target code generation.

## Definition of success for v0.1

The version 0.1 architecture is proven when:

- A synthetic source artifact is adapted into a schema-valid model.
- The model is translated into executable DuckDB SQL.
- The SQL produces the expected data in an isolated test.
- Every operation has a traceable evidence record.
- A target-unsupported operation blocks runnable output and appears in both JSON and Markdown
  evidence.
- All examples and tests run without external services or private data.

