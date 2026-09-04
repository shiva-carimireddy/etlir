# ETLIR Migration Evidence

**Status:** `complete`  
**Run ID:** `run_1d09dc0bfd150a80`  
**Created:** `2026-09-04T12:00:00Z`  
**Model version:** `0.1.0`

## Translation

- Source Adapter: `exampleflow 1.0.0`
- Target Adapter: `duckdb 1.0.0`

## Summary

| Measure | Count |
|---|---:|
| Source Elements | 4 |
| Model Operations | 4 |
| Preserved | 4 |
| Approximated | 0 |
| Unsupported | 0 |
| Ambiguous | 0 |
| Manual | 0 |
| Blocked | 0 |

## Operation records

| Model operation | Disposition | Target location | Basis |
|---|---|---|---|
| `read_orders` | `preserved` | `target/pipeline.sql#cte:read_orders` | CSV read and declared field types translated to DuckDB read_csv. |
| `keep_completed` | `preserved` | `target/pipeline.sql#cte:keep_completed` | Filter expression translated to a DuckDB WHERE predicate. |
| `calculate_net` | `preserved` | `target/pipeline.sql#cte:calculate_net` | Derived fields translated to DuckDB SELECT expressions. |
| `write_orders` | `preserved` | `target/pipeline.sql#statement:copy:write_orders` | Parquet write translated to a DuckDB COPY statement. |

## Diagnostics

No diagnostics.
## Interpretation boundary

This evidence accounts for declared model operations and adapter translation rules. It is not proof of runtime or business equivalence. Data reconciliation, target testing, security review, and owner acceptance remain necessary.
