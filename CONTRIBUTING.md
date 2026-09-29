# Contributing to ETLIR

Thank you for your interest. ETLIR welcomes contributions from individuals and companies:
new source adapters, new target emitters, construct support for existing pairs, test
fixtures, documentation, and bug reports.

## Ground rules

1. **Developer Certificate of Origin (DCO).** Every commit must be signed off
   (`git commit -s`), certifying the [DCO 1.1](https://developercertificate.org/): you
   wrote the change or otherwise have the right to submit it under Apache-2.0. There is
   no CLA. Contributions are licensed inbound under Apache-2.0, the same license as the
   project (Apache-2.0 §5).
2. **No confidential material.** Never contribute employer or customer code, metadata,
   exports, schemas, connection details, or data, even anonymized, unless you hold
   written permission to publish it under Apache-2.0. Use original synthetic fixtures.
3. **Only clean-room vendor knowledge.** Implement source formats from public
   documentation, files you are licensed to share, and observed behavior. Do not copy
   proprietary SDK code, documentation text, or sample files that forbid redistribution.
4. **Honest claims.** Follow [docs/claims-policy.md](docs/claims-policy.md). A PR may
   not describe a construct as supported unless the capability manifest declares it and
   tests demonstrate it.

## Kinds of contribution

| Contribution | Start with | Must include |
|---|---|---|
| Bug fix | Issue (bug report) | Regression test |
| Construct support in an existing pair | Issue ("construct support") | Normalization/lowering rule, manifest entry, positive + negative tests, fixture with independent expected output |
| New source adapter | Issue ("new source adapter") → design discussion | Passes `etlir.testing.check_source_adapter`; Raw IR omissions documented; security review of parser |
| New target emitter | Issue ("new target emitter") → design discussion | Passes `etlir.testing.check_target_emitter`; capability manifest; pinned runtime; execution tests |
| Canonical IR change | Architecture Decision Record (ADR) in `docs/decisions/` | Version bump per ADR-0004, regenerated schema, migration notes, maintainer approval |

**In-tree or separate package?** Both are first-class. In-tree plugins (under
`src/etlir/sources/` or `src/etlir/targets/`) are maintained with the core and must meet
its CI and review bar. Independent packages (`etlir-source-<name>`,
`etlir-target-<name>`) register through entry points and can be listed in the support
matrix once they publish a passing conformance report. Vendor-maintained plugins are
welcome in either form.

## Canonical IR changes

The Canonical IR is the project's central contract. Changes must:

* keep it source- and target-neutral: no product names, native type names, or
  target-specific fields ([tests/test_boundaries.py](tests/test_boundaries.py));
* be motivated by at least one concrete construct, and explain how existing adapters and
  emitters are affected;
* follow the versioning rules in
  [ADR-0004](docs/decisions/0004-versioning-and-compatibility.md).

## Development

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff format src tests && ruff check src tests
mypy
pytest
etlir schema --out schemas/canonical   # after any Canonical IR model change
```

Generated artifacts must be deterministic. Use `etlir.serialization.dumps`/`write_json`
and emit collections in a stable order.

## Pull requests

* Keep PRs focused; one construct or one concern per PR where possible.
* Fill in the PR template, including the claims checklist.
* CI must pass. At least one maintainer approval is required; changes to the Canonical
  IR, contracts, or evaluation protocol need a maintainer approval plus an ADR.

## Evaluation and research artifacts

Anything that feeds published numbers (benchmark corpus, comparator policies,
expectations, the reporting script) follows [benchmarks/protocol.md](benchmarks/protocol.md).
Changes after a protocol freeze require a new protocol version, not an edit in place.

## Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
