# ADR 0001: Public terminology

- **Status:** Accepted
- **Date:** 2026-09-04

## Context

The project needs terminology that is technically accurate, understandable to adapter authors,
and usable beyond a single migration platform.

## Decision

Use ETLIR as the project name. Name the neutral contract the **Pipeline Semantic Model**. Use
**Source Adapter**, **Target Adapter**, **Model Validation**, **Fidelity Assessment**,
**Diagnostic**, and **Migration Evidence Bundle** for the lifecycle concepts.

## Consequences

Documentation and APIs use one symmetric adapter vocabulary. Model validity is distinct from
translation fidelity. ETLIR remains recognizable while the model name does not imply that it is a
universal canonical representation.

