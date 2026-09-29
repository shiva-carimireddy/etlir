# Security Policy

ETLIR parses untrusted input (exported ETL definitions) and generates executable code,
so parser and code-generation safety are in scope.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/shiva-carimireddy/etlir/security/advisories/new)
or email **shivacarimireddy@ieee.org**. Expect an acknowledgement within 7 days. Please
include a minimal reproducing input.

## Supported versions

Until 1.0, only the latest release receives security fixes.

## Security requirements for contributions

* **XML and other parsers** must disable external entity resolution, DTD loading and
  network access, and bound input size and entity expansion (use `defusedxml` or an
  equivalently hardened configuration). Malformed-input and XXE tests are required.
* **Generated code** must never interpolate unvalidated source text into executable
  code. Expressions are lowered from the typed Canonical IR AST; opaque source text is
  blocked, not pasted.
* **No secrets in artifacts.** Canonical IR bindings and generated packages hold
  references (for example, environment variable names), never credential values.
  Sensitive parameters carry no defaults (invariant `IR-V-009`).
* Fixtures and corpora must be free of credentials, personal data, and confidential
  metadata.
