# Adapter Development

ETLIR adapters are small packages with explicit inputs, outputs, capabilities, and diagnostics.
They do not share private state with the translation engine.

## Source Adapter contract

A Source Adapter implements:

```python
class SourceAdapter(Protocol):
    descriptor: AdapterDescriptor

    def adapt(self, source_path: Path) -> AdaptationResult:
        ...
```

`AdaptationResult` contains the model, a source-element inventory, diagnostics, and source
artifact metadata. Inventory every source operation before adapting it. Each inventory pointer
must then appear in a model operation's `source_refs` or in a source-stage diagnostic.

The adapter must return `model=None` when it cannot construct a valid model without inventing or
discarding behavior.

## Target Adapter contract

A Target Adapter implements:

```python
class TargetAdapter(Protocol):
    descriptor: AdapterDescriptor

    def translate(self, model: dict[str, object]) -> TranslationResult:
        ...
```

`TranslationResult` contains generated artifacts, translation records, and diagnostics. There
must be one record for every model operation. A record states its disposition, target locations,
and basis.

Perform capability checks before producing runnable output. If any operation is unsupported,
return `unsupported` for that operation, `blocked` for operations not emitted because of the
failure, and no runnable artifact.

## Registration

Register third-party adapters in `pyproject.toml`:

```toml
[project.entry-points."etlir.source_adapters"]
my_source = "my_package.source:MySourceAdapter"

[project.entry-points."etlir.target_adapters"]
my_target = "my_package.target:MyTargetAdapter"
```

Names must be lowercase and stable. The adapter descriptor separately declares adapter version,
kind, and supported model versions.

## Error handling

Use diagnostics for expected limitations or invalid input. Raise exceptions only for programming
errors or unexpected environmental failures. Never place credentials, full connection strings,
or sensitive source fragments in a diagnostic.

## Minimum adapter test suite

A contributed adapter must test:

- A successful minimal artifact.
- Every claimed operation capability.
- Invalid source or model input.
- At least one unsupported construct.
- Complete source or model accounting.
- Deterministic output for identical input.
- Output path containment and secret-safe diagnostics.

## Compatibility

An adapter must reject unsupported model versions. Supporting `0.1.0` does not automatically mean
support for `0.2.0`. Patch releases of the reference package may fix implementation bugs without
changing the model contract.

