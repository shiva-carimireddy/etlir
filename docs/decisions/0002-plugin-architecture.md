# ADR-0002: Entry-point plugins, enforced boundaries, conformance kit

**Status:** Accepted, 2026-09-28

## Context
The research claim is that source adapters and target emitters evolve independently
around a shared Canonical IR. Convention alone erodes. The boundary must be testable, and
third parties must be able to ship plugins without forking.

## Decision
* Plugins are Python classes implementing `SourceAdapter` or `TargetEmitter`, discovered
  through the `etlir.sources` and `etlir.targets` entry-point groups. In-tree plugins use
  the same mechanism.
* The registry rejects plugins with invalid ids, wrong base class, or incompatible IR
  versions. Broken plugins are reported, not fatal.
* Import boundaries (core ↛ plugins, sources ↛ targets, plugin ↛ sibling plugin) and the
  vendor-neutrality of the canonical schema are enforced by `tests/test_boundaries.py`.
* `etlir.testing` provides a conformance kit that plugins run in their own CI.
* Capabilities are declared data (`CapabilityManifest`); undeclared constructs are
  blocked.

## Consequences
Contract changes affect every plugin, so they require an ADR and a version bump
(ADR-0004). Passing conformance does not establish semantic correctness.
