# Retail orders example

This synthetic ExampleFlow pipeline reads four fictional retail orders, keeps the two rows whose
status is `COMPLETE`, derives `net_amount`, and describes a Parquet target.

From the repository root, generate the model, DuckDB SQL, and evidence:

```bash
etlir translate \
  --source exampleflow \
  --target duckdb \
  examples/retail-orders/source.exampleflow.json \
  --output build/retail-orders
```

The command translates only. The end-to-end test separately executes the generated SQL against
DuckDB and verifies rows `(1001, 110.00)` and `(1003, 45.00)`.

All names and values in this example are fictional.

