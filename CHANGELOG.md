# Changelog

All notable changes to ETLIR will be documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases will use semantic
versioning after the public API stabilizes.

## [Unreleased]

## [0.1.0-alpha.1] - 2026-09-04

### Added

- Pipeline Semantic Model v0.1 specification and JSON Schema.
- Versioned diagnostic and Migration Evidence Bundle contracts.
- Source and target adapter interfaces with Python entry-point discovery.
- Synthetic ExampleFlow v1 source adapter.
- DuckDB SQL target adapter.
- CLI translation flow with machine-readable and Markdown evidence.
- Successful and unsupported end-to-end examples.
- CI-gated GitHub release automation for explicit release commits.

### Changed

- Updated the GitHub Actions runtime dependencies to `actions/checkout@v7` and
  `actions/setup-python@v7`.
- Expanded the development constraint to allow mypy 2.x after the full test matrix passed.

[Unreleased]: https://github.com/shiva-carimireddy/etlir/compare/v0.1.0-alpha.1...HEAD
[0.1.0-alpha.1]: https://github.com/shiva-carimireddy/etlir/releases/tag/v0.1.0-alpha.1
