# ETLIR Specifications

This directory contains implementation-independent ETLIR contracts. A version directory is
immutable after a stable release except for non-normative clarification and corrections that do
not change accepted documents.

## Available versions

| Version | Status | Contents |
|---|---|---|
| [`v0.1`](v0.1/) | Draft / pre-alpha | Pipeline Semantic Model, Diagnostic, and Migration Evidence Bundle |

JSON Schemas are normative for document structure. The accompanying specification defines
semantics and invariants that JSON Schema alone cannot express.

Model and evidence format versions are independent of the Python package version. An adapter must
declare the exact model versions it supports.

