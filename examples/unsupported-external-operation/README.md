# Unsupported external operation

This synthetic ExampleFlow pipeline contains an HTTP enrichment step. The Pipeline Semantic Model
can represent that operation, but the DuckDB SQL adapter cannot implement it.

Running the fixture demonstrates the failure contract:

```bash
etlir translate \
  --source exampleflow \
  --target duckdb \
  examples/unsupported-external-operation/source.exampleflow.json \
  --output build/unsupported
```

Expected behavior:

- The source operation remains present as `external_operation` in `pipeline-model.json`.
- Evidence contains `TARGET.UNSUPPORTED_OPERATION` and an `unsupported` disposition.
- Other operations receive `blocked` dispositions because no runnable artifact is emitted.
- The CLI exits with status `2`.
- `target/pipeline.sql` is absent.

The `.example.test` URL is reserved for examples and is never contacted.

