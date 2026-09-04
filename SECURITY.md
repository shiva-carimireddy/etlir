# Security Policy

## Supported versions

ETLIR is pre-alpha. Security fixes are applied to the latest development version. No released
version currently carries a long-term support commitment.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability
reporting feature for this repository. Include:

- The affected version or commit.
- Reproduction steps or a minimal proof of concept.
- The likely impact.
- Any suggested mitigation, if known.

You should receive an acknowledgement within seven days. A confirmed issue will be assessed,
fixed privately when practical, and disclosed with appropriate credit unless anonymity is
requested.

## Security scope

ETLIR parses untrusted pipeline descriptions and writes generated artifacts. Adapters must not:

- Execute source-provided code during adaptation.
- Follow arbitrary network URLs while parsing.
- Write outside the selected output directory.
- Include credentials or secrets in diagnostics or evidence.
- Treat generated target artifacts as safe to run without review.

