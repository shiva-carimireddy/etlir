# ADR 0003: Make semantic accounting mandatory

- **Status:** Accepted
- **Date:** 2026-09-04

## Context

Migration tools commonly focus on generated output. Unsupported or unrecognized behavior can
then disappear without an inspectable record.

## Decision

Every source operation must map to a model operation or a source-stage diagnostic. Every model
operation must receive a target translation record. The Fidelity Assessor treats an accounting
gap as an internal error and blocks the run.

Unsupported target behavior receives an `unsupported` record and error diagnostic. The reference
engine withholds runnable target artifacts for a blocked run.

## Consequences

Adapter authors perform more explicit bookkeeping, but users can distinguish preservation,
approximation, ambiguity, manual work, and unsupported behavior. A generator cannot report
success merely because it produced a file.

