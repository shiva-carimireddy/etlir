# Migration Evidence Bundle

A Migration Evidence Bundle records what ETLIR processed and how every model operation was
handled. It is evidence for review, not a certificate of equivalence.

## Files

A normal successful run contains:

```text
output/
├── pipeline-model.json
├── target/
│   └── pipeline.sql
├── evidence.json
└── evidence.md
```

When translation is blocked, the model and evidence remain, while runnable target artifacts are
withheld.

## JSON contents

The JSON document includes:

- Evidence format version and ETLIR version.
- A deterministic run identifier.
- Source and target adapter descriptors.
- Input and generated artifact hashes.
- Model validation status.
- One translation record for every model operation.
- Structured diagnostics.
- Counts by disposition and the overall status.

Each translation record connects:

```text
source JSON Pointer → model operation → target artifact locator or diagnostic
```

The `basis` field states the rule or limitation behind the disposition. It should be specific
enough for a reviewer to understand why the adapter used that classification.

## Integrity

Artifact digests use SHA-256 over exact bytes. Paths in evidence are relative logical paths, not
machine-specific absolute paths. The evidence document does not hash itself.

The run identifier is derived from source digest, adapter identities, target identity, and model
version. Repeating the same translation inputs and tool versions therefore yields the same run
identifier, while `created_at` records the actual execution time.

## Human-readable rendering

`evidence.md` is generated from the JSON evidence. It includes status, provenance, summary,
operation records, and diagnostics. The Markdown form must not introduce facts absent from the
JSON form.

## Interpretation

- `complete` means all modeled operations received a `preserved` disposition and no errors were
  found.
- `qualified` means translation completed with a known approximation, ambiguity, or manual step.
- `blocked` means runnable target output was withheld or the accounting invariant failed.

None of these statuses replaces data reconciliation, runtime tests, performance tests, security
review, or business-owner acceptance.

