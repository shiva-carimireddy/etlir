# ADR-0006: DuckDB as a second emitter and the zero-JVM local profile

**Status:** Accepted, 2026-09-28 (implements item 5 of ADR-0005)

## Context
A single target cannot show that the Canonical IR is target-independent, and Spark needs
a JVM, which raises the setup cost for new users and CI.

## Decision
Ship a `duckdb` emitter alongside `spark` in 0.1. It lowers the same Canonical IR to static
SQL through an independent code path, shares only target-neutral core code
(`canonical.plan`, `workflow`, `package`, `runner`), and runs without Java. Both emitters
must satisfy the same capability manifest (a test enforces equality in 0.1), and every
benchmark case runs on both.

## Consequences
Differences between the engines surface as test failures. Three have already been found
and resolved in the canonical semantics (ADR-0007). Portability claims remain limited:
both emitters share one author, one source adapter and a small construct subset.
