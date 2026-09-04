# Diagnostics

Diagnostics are public, versioned data. They must be usable by people, CI systems, and tools that
aggregate migration results.

## Required fields

Every diagnostic includes:

- A deterministic `id` within the run.
- A stable `code` suitable for automation.
- `severity`: `info`, `warning`, or `error`.
- `category`: `invalid`, `unsupported`, `ambiguous`, `lossy`, `manual_action`, or `internal`.
- The lifecycle `stage` that produced it.
- A concise `message`.
- A location containing an artifact and, when available, a JSON Pointer and operation identifier.
- A remediation statement when a user can take action.

Diagnostic IDs are derived from stable diagnostic content. Codes remain stable across patch
releases unless their meaning changes.

## Initial codes

| Code | Meaning |
|---|---|
| `SOURCE.INVALID_JSON` | The source artifact is not valid JSON. |
| `SOURCE.SCHEMA_INVALID` | The source artifact violates the selected source format contract. |
| `SOURCE.UNACCOUNTED_OPERATION` | A Source Adapter lacks a rule for a source operation it accepted. |
| `MODEL.SCHEMA_INVALID` | The Pipeline Semantic Model violates its JSON Schema. |
| `MODEL.DUPLICATE_ID` | A model identifier is not unique in its collection. |
| `MODEL.DUPLICATE_EDGE` | The graph declares the same directed edge more than once. |
| `MODEL.INVALID_REFERENCE` | An operation, edge, or dataset reference cannot be resolved. |
| `MODEL.CYCLE` | The operation graph contains a cycle. |
| `TARGET.UNSUPPORTED_MODEL_VERSION` | The adapter does not accept the model's declared version. |
| `TARGET.UNSUPPORTED_OPERATION` | The target cannot represent a model operation. |
| `TARGET.UNSUPPORTED_SHAPE` | The graph shape is outside target adapter capabilities. |
| `TARGET.UNSUPPORTED_DATASET` | A dataset kind or format is unsupported. |
| `TARGET.UNSUPPORTED_EXPRESSION` | An expression cannot be represented safely. |
| `FIDELITY.UNACCOUNTED_SOURCE` | A source operation has neither a model reference nor diagnostic. |
| `FIDELITY.UNACCOUNTED_MODEL` | A model operation has no target disposition. |
| `FIDELITY.DUPLICATE_MODEL_RECORD` | A model operation received more than one target record. |
| `FIDELITY.UNKNOWN_MODEL_RECORD` | A target record references an operation absent from the model. |
| `FIDELITY.PRESERVED_WITHOUT_TARGET` | A preserved record lacks a target artifact reference. |
| `FIDELITY.UNSUPPORTED_WITHOUT_DIAGNOSTIC` | An unsupported record lacks a linked explanation. |
| `FIDELITY.UNKNOWN_TARGET_REFERENCE` | A record points to an artifact the adapter did not return. |
| `FIDELITY.UNKNOWN_DIAGNOSTIC_REFERENCE` | A record links to a diagnostic absent from the run. |

Adapters may add namespaced codes, but they must not redefine core codes.

## Severity and run status

An error blocks the run. A warning can produce a `qualified` result when every operation remains
accounted for. Informational diagnostics do not change status.

Unsupported behavior is an error in v0.1. The CLI still writes evidence so that a failed
translation remains inspectable.
