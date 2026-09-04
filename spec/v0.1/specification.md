# Pipeline Semantic Model Specification v0.1.0

**Status:** Draft / pre-alpha  
**Schema dialect:** JSON Schema Draft 2020-12

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** indicate requirement
strength for implementations of this specification.

## 1. Purpose

The Pipeline Semantic Model represents declared data-pipeline behavior without requiring the
source or target platform at interpretation time. It is an exchange and analysis contract, not an
execution plan.

Version 0.1 deliberately models a small batch-processing subset. A valid document may contain an
operation that a particular Target Adapter cannot translate.

## 2. Document structure

A model document contains:

- `model_version`: exactly `0.1.0`.
- `provenance`: the Source Adapter and source artifact identity.
- `pipeline`: identity, datasets, parameters, operations, and directed edges.

Unknown properties are invalid. Future extensions require a new compatible specification or an
explicit extension mechanism.

## 3. Identifiers and references

Identifiers are document-local, case-sensitive strings. Dataset IDs MUST be unique among
datasets. Operation IDs MUST be unique among operations. Parameter IDs MUST be unique among
parameters.

Every edge endpoint MUST resolve to an operation. Every dataset reference MUST resolve to a
dataset. Every parameter expression MUST resolve to a parameter. The operation graph MUST be
acyclic.

## 4. Provenance

`provenance.source_adapter` identifies the adapter name and version. `source_artifact` contains a
logical filename and SHA-256 digest of the exact input bytes. Absolute local paths MUST NOT appear
in portable model documents.

Each operation contains at least one `source_refs` entry. A source reference identifies the
source artifact and a JSON Pointer locating the source construct represented by that operation.

## 5. Datasets

A dataset declares:

- `id` and optional human-readable `name`.
- `role`: `source`, `target`, or `intermediate`.
- `kind`: `file`, `table`, `stream`, or `other`.
- `format`: an adapter-defined lowercase identifier.
- `location.uri`: a logical location that is not resolved by the model.
- Optional ordered field declarations.

Dataset declarations do not contain credentials. Connection identity and runtime secret handling
are outside v0.1.

## 6. Parameters

Parameters declare an ID, type, required flag, and optional default. Version 0.1 defines parameter
representation, but the built-in DuckDB Target Adapter does not translate parameter expressions.

## 7. Operations

### 7.1 Read

A `read` operation introduces data from one referenced dataset. The referenced dataset SHOULD
have role `source` or `intermediate`.

### 7.2 Filter

A `filter` operation retains input rows for which `predicate` evaluates to true. Null and
three-valued logic follow the source semantics recorded by an adapter; if those semantics cannot
be represented, the target result MUST be approximate, ambiguous, unsupported, or manual rather
than preserved.

### 7.3 Derive

A `derive` operation adds or replaces named fields using ordered assignments. Each expression is
evaluated against the operation input. An adapter MUST document whether later assignments can
reference earlier assignments; the v0.1 built-in adapters do not allow that behavior.

### 7.4 Write

A `write` operation sends its input to one referenced dataset. Write mode and transactional
behavior are not modeled in v0.1; adapters MUST NOT claim preservation when the source depends on
such behavior.

### 7.5 External operation

An `external_operation` records behavior performed outside ordinary relational transformation,
such as an HTTP request. `configuration` contains non-secret declared properties. Presence in the
model does not imply target support.

## 8. Expressions

Version 0.1 expressions are trees with one of four kinds:

- `field`: reference to an input field.
- `literal`: typed scalar or null value.
- `parameter`: reference to a declared parameter.
- `binary`: an operator with `left` and `right` expressions.

Binary operators are `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `add`, `subtract`, `multiply`,
`divide`, `and`, and `or`. Target Adapters MUST classify a translation according to their actual
operator and null semantics.

## 9. Edges and graph validity

Edges express operation ordering and data dependency. Parallel edges with the same `from` and
`to` values are invalid. A non-read operation SHOULD have at least one incoming edge. A write
operation SHOULD be terminal.

The core model allows branching and multiple writes. Individual Target Adapters may support a
narrower graph shape and MUST diagnose unsupported shapes.

## 10. Validation

An implementation validates a model in this order:

1. Validate against `schemas/pipeline-model.schema.json`.
2. Enforce unique identifiers.
3. Resolve dataset, parameter, and edge references.
4. Detect duplicate edges and graph cycles.
5. Apply operation-specific semantic checks.

A validation failure produces one or more diagnostics and prevents target translation.

## 11. Fidelity accounting

For every operation in `pipeline.operations`, a Target Adapter MUST return exactly one translation
record. Its disposition is one of:

- `preserved`
- `approximated`
- `unsupported`
- `ambiguous`
- `manual`
- `blocked`

A preserved record MUST reference at least one target artifact location. An unsupported record
MUST be accompanied by an error diagnostic. A blocked record means target generation was withheld
because another blocking condition exists.

Source-to-model accounting is based on the Source Adapter's explicit source-element inventory.
An inventory element MUST be referenced from the model or covered by a source diagnostic.

## 12. Compatibility

The `0.1.0` model version is exact. Consumers MUST reject unsupported model versions. A future
patch revision may clarify prose but cannot change schema-valid document meaning. Additive or
breaking model changes require a new declared version and compatibility guidance.

## 13. Security and privacy

Model documents and evidence MUST NOT contain secrets. Adapters MUST treat input expressions and
locations as data, not executable instructions. A generated target artifact is untrusted until it
has passed target-specific review and testing.

