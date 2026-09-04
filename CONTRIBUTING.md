# Contributing to ETLIR

Thank you for helping make pipeline migrations more inspectable.

## Before contributing

Read the [clean-room policy](docs/clean-room-policy.md). Contributions must not contain employer,
client, or other confidential artifacts or implementation details. When adapting a public format,
link to the public specification and confirm that any included fixtures can be redistributed.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

## Pull requests

Keep pull requests focused and include:

- The problem and intended behavior.
- Tests covering the success and diagnostic paths.
- Specification changes when public model behavior changes.
- An Architecture Decision Record for changes that alter contracts or invariants.
- Documentation for new adapter capabilities and limitations.

Generated output is not enough by itself. A target adapter must account for every model operation
with a disposition and must return a structured diagnostic for unsupported, approximate,
ambiguous, or manual behavior.

## Specification changes

The specification is independent of the Python reference implementation. Changes under `spec/`
must explain compatibility impact and update the JSON Schema, examples, and validation tests.
Breaking changes require a new specification version.

## Adapter naming

Use the public platform or format name only when the adapter is based on public documentation and
doing so complies with applicable trademark guidance. Synthetic adapters must be clearly labeled
as synthetic.

## Clean-room attestation

By submitting a contribution, you confirm that it is original or appropriately licensed, that
you have the right to contribute it under Apache-2.0, and that it does not disclose confidential
or proprietary information.

