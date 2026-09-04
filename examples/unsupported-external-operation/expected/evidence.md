# ETLIR Migration Evidence

**Status:** `blocked`  
**Run ID:** `run_0833e66943b9d9aa`  
**Created:** `2026-09-04T12:00:00Z`  
**Model version:** `0.1.0`

## Translation

- Source Adapter: `exampleflow 1.0.0`
- Target Adapter: `duckdb 1.0.0`

## Summary

| Measure | Count |
|---|---:|
| Source Elements | 3 |
| Model Operations | 3 |
| Preserved | 0 |
| Approximated | 0 |
| Unsupported | 1 |
| Ambiguous | 0 |
| Manual | 0 |
| Blocked | 2 |

## Operation records

| Model operation | Disposition | Target location | Basis |
|---|---|---|---|
| `read_orders` | `blocked` | — | No target artifact was emitted because the pipeline contains a blocking condition. |
| `fetch_loyalty_tier` | `unsupported` | — | The target adapter reported an unsupported capability for this operation. |
| `write_orders` | `blocked` | — | No target artifact was emitted because the pipeline contains a blocking condition. |

## Diagnostics

### `TARGET.UNSUPPORTED_OPERATION`

- Severity: `error`
- Category: `unsupported`
- Stage: `target_translation`
- Location: `pipeline-model.json/pipeline/operations/1`
- Message: DuckDB SQL cannot preserve external operation 'fetch_loyalty_tier' (http_request).
- Remediation: Implement the operation outside SQL or choose a target adapter with this capability.

## Interpretation boundary

This evidence accounts for declared model operations and adapter translation rules. It is not proof of runtime or business equivalence. Data reconciliation, target testing, security review, and owner acceptance remain necessary.
