# ADR-0007: Resolve engine divergences in the canonical semantics, not per target

**Status:** Accepted, 2026-09-28

## Context
Probing Spark 4.2 and DuckDB 1.5 showed different results for the same logical operation:
decimal→integer casts (Spark truncates, DuckDB rounds), `substring` with position 0,
quoted empty CSV fields (Spark NULL, DuckDB `''`), and division by zero (Spark ANSI error,
DuckDB `inf`).

## Decision
The canonical semantics fix one behavior for each (see docs/semantics.md): casts to
integral types round half away from zero, substring positions follow the documented rule,
empty CSV fields are NULL, division by zero is NULL. Emitters generate explicit code for
these behaviors instead of relying on engine defaults. A truth-table case
(`pc-expression-semantics`) pins them on every target.

## Consequences
Generated code is slightly more verbose (explicit `round`, position normalization). A new
target must pass the same truth table. Where the *source* platform's behavior differs from
the canonical choice, the adapter records an approximation instead of changing canonical
semantics.
