# Benchmark cases

Each directory is one case: `case.toml` (manifest), `source/` (PowerCenter-format export),
`inputs/` (controlled input data), `expected/` (expected outputs). Run them with
`etlir benchmark benchmarks/cases --out <dir>`.

## Provenance

All cases are **original synthetic fixtures** written for ETLIR and licensed Apache-2.0.
The XML follows the structure of public PowerCenter exports (element and attribute
vocabulary observed in the pinned public corpus) but contains no copied third-party or
employer content. The data is invented.

## How expectations were authored

Expected outputs were written by hand from each fixture's stated behavior and
[docs/semantics.md](../../docs/semantics.md), not copied from any emitter's output. Each
case below lists the behaviors it pins.

| Case | Pins |
|---|---|
| `pc-orders` | Cross-file session→mapping resolution (mappings and workflow in separate exports); trimming and NULL-safe concatenation; stateless variable port; filter with NULL keys; router with a mapping parameter, NULL falling to the default group, a lowercase status excluded; aggregation ignoring NULLs; master-outer join keeping unmatched and NULL-key rows; unmapped target column written as NULL. |
| `pc-mixed-blocked` | Fail-closed behavior: a connected lookup, a row-aligned merge, a SQL override, a stateful variable port, a command task and a custom link condition are all blocked with reasons; the independent clean branch runs with `allow_partial`. |
| `pc-expression-semantics` | A truth table for rounding (±.5), `SUBSTR` positions (0, negative, clamped, length ≤ 0, NULL length), concatenation with NULLs, division by zero, NULL conditions in `IIF`, empty CSV fields. |

### Change log of expectations

* `pc-orders`: after mutation analysis showed that replacing `COUNT(ORDER_ID)` with
  `COUNT(*)` went undetected, an order with a NULL `ORDER_ID` was added to
  `inputs/orders.csv`, and its effects were derived by hand: one more standard-band fact
  row, and customer 3's `TOTAL_GROSS` becomes 109.99 while `ORDER_COUNT` stays 1.
