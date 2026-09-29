## What and why

<!-- One construct or one concern per PR where possible. Link the issue. -->

## Kind

- [ ] Bug fix
- [ ] Construct support (existing pair)
- [ ] New source adapter / target emitter
- [ ] Canonical IR / contract change (ADR: `docs/decisions/____`)
- [ ] Docs / tooling

## Checklist

- [ ] All commits are signed off (`git commit -s`, DCO)
- [ ] No confidential, employer, or customer material; fixtures are original or licensed for redistribution
- [ ] Tests added: positive **and** negative/blocked cases
- [ ] Capability manifest updated; no construct described as supported without tests ([claims policy](../docs/claims-policy.md))
- [ ] Generated artifacts are deterministic
- [ ] `etlir schema` rerun if the Canonical IR changed (and IR version bumped per ADR-0004)
- [ ] CHANGELOG updated
