# Clean-room Development Policy

ETLIR is developed as an independent public implementation. Professional experience may motivate
the general problem, but private implementation details must not enter the project.

## Allowed sources

Contributions may use:

- Original designs and code written for ETLIR.
- Public standards and public product documentation.
- Publicly licensed source code when its license is compatible and attribution is preserved.
- Synthetic examples created specifically for this repository.
- Contributor-owned artifacts that the contributor has the right to publish.

## Prohibited material

Do not contribute or paraphrase:

- Employer or client source code.
- Internal schemas, API contracts, architecture documents, or repository structures.
- Internal platform, program, or project names.
- Real workflow exports, logs, screenshots, identifiers, metrics, or configuration values.
- Proprietary transformation rules or operating procedures.
- Confidential migration findings or target limitations.
- Content produced by closely copying a non-public implementation.

Removing names from confidential material does not make it acceptable.

## Adapter provenance

Every non-synthetic adapter proposal must identify the public documentation used to design it.
Fixtures must be synthetic or redistributable. When a format is reverse-engineered from a public
artifact, the contribution must document why the artifact may be used and must avoid protected
secrets or personal data.

## Review checklist

Reviewers should verify that:

- Names, example values, and domains are fictional.
- No comments refer to a contributor's private system.
- Diagnostic messages reveal no credentials or internal identifiers.
- Tests can be executed from public repository contents alone.
- Attribution and compatible licenses are present where needed.

If provenance is uncertain, stop the contribution until it can be established. Maintainers may
remove material later if a confidentiality or licensing concern is discovered.

