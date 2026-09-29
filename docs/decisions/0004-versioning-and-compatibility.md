# ADR-0004: Versioning of Canonical IR, adapters, and emitters

**Status:** Accepted, 2026-09-28

## Decision
* **Package** (`etlir`): Semantic Versioning.
* **Canonical IR** (`IR_VERSION`, independent of the package version):
  * Before 1.0, any schema change bumps the **minor** version. Patch versions are for
    documentation or clarification only, with no schema change.
  * After 1.0: additive optional fields → minor; anything that can invalidate an existing
    document or change its meaning → major.
  * Each released IR version keeps its schema file in `schemas/canonical/`. A test fails
    if the model and the committed schema disagree.
* **Source adapters** declare the IR version they produce. Registration requires an
  equal major.minor.
* **Target emitters** declare a PEP 440 specifier of IR versions they accept.
* **Adapters/emitters** bump their own version whenever a normalization or lowering rule
  changes observable output, so evidence and results can cite exact rule versions.

## Consequences
Every artifact records tool, IR, adapter, and emitter versions in its run manifest.
Pre-1.0 minor bumps may require plugin updates.
