# Roadmap

The roadmap describes direction, not a delivery promise. Each milestone must remain usable and
tested on its own.

## v0.1 — Architecture proof

- Publish Pipeline Semantic Model v0.1.
- Prove one complete ExampleFlow-to-DuckDB path.
- Enforce source-to-model and model-to-target accounting.
- Produce JSON and Markdown migration evidence.
- Block runnable output when a target cannot represent an operation.
- Document the clean-room and third-party adapter contracts.

## v0.2 — Semantic depth

- Add joins, projections, unions, typed parameters, and explicit null behavior.
- Define approximation policies and adapter capability declarations.
- Add model upgrade tooling between compatible specification versions.
- Add evidence comparison between two translation runs.
- Add one adapter based exclusively on a public, redistributable format.

## v0.3 — Control-flow context

- Model branching conditions, retry policies, and error routes.
- Represent external operations and stored procedures more precisely.
- Add orchestration and dependency context without becoming a scheduler.
- Define extension namespaces and compatibility rules.

## Future exploration

- Streaming, CDC, and managed file-transfer semantics.
- Upstream and downstream relationship exchange.
- Runtime reconciliation signals linked back to model elements.
- Detection of drift after target artifacts are edited outside ETLIR.
- A public registry of independently maintained adapters.

## Explicit non-goals

- Replacing target execution engines or orchestrators.
- Claiming full automatic migration for arbitrary platforms.
- Treating successful generation as proof of business equivalence.
- Adding many nominal adapters before the contracts are proven.

