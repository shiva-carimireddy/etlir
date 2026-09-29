# Extending ETLIR: source adapters and target emitters

## Source adapter

Subclass `etlir.contracts.SourceAdapter`:

```python
class MyAdapter(SourceAdapter):
    id = "my-format"          # lowercase kebab-case, globally unique
    version = "0.1.0"         # adapter version; bump when normalization rules change
    ir_version = "0.1.0"      # Canonical IR version produced (major.minor must match)

    def accepts(self, path): ...                 # cheap sniffing
    def load(self, inputs, root) -> RawBundle: ...
    def normalize(self, raw) -> NormalizationResult: ...
```

Requirements:

* `load` parses **all inputs of a corpus group together** and resolves cross-file
  references against the whole group. Report missing companions separately from parse
  failures.
* Raw IR records input paths and SHA-256 digests, keeps unknown native attributes, and
  lists intentional `omissions`.
* Every canonical entity carries a `SourceRef` with `adapter == id`, a precise locator
  (e.g. XPath), and the normalization `rule` id.
* Unknown or unsupported constructs become `UnsupportedOp`, `TaskKind.UNSUPPORTED`, or
  `OpaqueNode`. They are never skipped.
* Parsers are hardened (see [SECURITY.md](../SECURITY.md)).
* Output is deterministic: sort by stable keys, never by hash or dict order of input.

## Target emitter

Subclass `etlir.contracts.TargetEmitter`:

```python
class MyEmitter(TargetEmitter):
    id = "my-target"
    version = "0.1.0"
    ir_versions = ">=0.1.0,<0.2"

    def capabilities(self) -> CapabilityManifest: ...
    def plan(self, document) -> dict: ...        # pure; must not mutate the document
    def write(self, plan, out_dir) -> EmitResult: ...
```

Requirements:

* Consume only the `CanonicalDocument`. Never import a source adapter or read source files.
* Declare every supported construct in the manifest. Use `constrained` with explicit,
  machine-checked preconditions rather than over-claiming `supported`.
* Refuse to emit runnable artifacts for blocked tasks; report them with source traces.
* Lower expressions from the typed AST. Never paste source expression text into target
  code.
* Pin the target runtime (versions, session settings affecting results) and record it
  in the run manifest.

## Registering

```toml
[project.entry-points."etlir.sources"]
my-format = "my_package.adapter:MyAdapter"

[project.entry-points."etlir.targets"]
my-target = "my_package.emitter:MyEmitter"
```

`etlir plugins` lists what is installed, including plugins rejected for IR-version
mismatch.

## Conformance

```python
from etlir.testing import check_source_adapter, check_target_emitter

def test_adapter_conforms(tmp_path):
    report = check_source_adapter(MyAdapter(), inputs, root)
    assert report.ok, report.failures
```

The kit checks the contracts (registration, determinism, provenance, schema round-trip,
invariants, non-mutation, blocked-construct reporting). It does **not** check that
translations are semantically correct. Each plugin needs its own semantic tests with
independently authored expected outputs.

## Getting listed in the support matrix

Open a "new source adapter" or "new target emitter" issue with the passing conformance
output, the capability manifest, and the fixtures used. Listings state the exact plugin
version and the constructs covered.
