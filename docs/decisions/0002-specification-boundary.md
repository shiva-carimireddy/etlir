# ADR 0002: Keep the specification implementation-independent

- **Status:** Accepted
- **Date:** 2026-09-04

## Context

Adopters may implement the model in languages other than Python. Tying the public contract to
Python classes would make independent implementations harder and risk two competing definitions.

## Decision

The normative model, diagnostic, and evidence contracts live under versioned `spec/` directories
and use JSON Schema. Python modules under `src/` are a reference implementation.

The packaged Python wheel may include an unchanged copy of the normative schemas for runtime
validation. Tests must verify that the implementation loads the same schema bytes found in
`spec/`.

## Consequences

Specification changes require explicit versioning and compatibility analysis. Python convenience
types cannot silently add fields or relax validation.

