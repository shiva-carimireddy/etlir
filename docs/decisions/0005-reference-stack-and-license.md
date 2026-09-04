# ADR 0005: Use Python, JSON Schema, and Apache-2.0

- **Status:** Accepted
- **Date:** 2026-09-04

## Context

The first implementation must be accessible to data engineers, easy to run locally, friendly to
third-party adapters, and independent from the model's implementation language.

## Decision

Use Python 3.11 or newer for the reference implementation. Use JSON Schema Draft 2020-12 for the
normative document contracts, Typer for the CLI, pytest for tests, and DuckDB for the first local
target. License the repository under Apache-2.0.

Avoid a second Python model-definition framework in v0.1 so JSON Schema remains the only normative
structural contract.

## Consequences

Developers can install and exercise the slice without external infrastructure. Independent tools
can consume the JSON contracts without importing Python. Apache-2.0 supplies a permissive license
and an explicit patent grant suitable for organizational adoption.

