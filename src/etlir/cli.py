"""Command-line entry point for the ETLIR reference implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from etlir import __version__
from etlir.adapters.registry import AdapterRegistry, UnknownAdapterError
from etlir.engine import ETLIREngineError
from etlir.engine import translate as run_translation

app = typer.Typer(
    name="etlir",
    help="Translate pipeline semantics and produce migration evidence.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = False,
) -> None:
    """ETLIR reference CLI."""


@app.command("translate")
def translate_command(
    source_path: Annotated[
        Path,
        typer.Argument(help="Path to the source pipeline artifact."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source Adapter name."),
    ],
    target: Annotated[
        str,
        typer.Option("--target", help="Target Adapter name."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="New or empty output directory."),
    ],
) -> None:
    """Adapt, validate, translate, assess, and write evidence."""

    try:
        result = run_translation(
            source_path=source_path,
            output_directory=output,
            source_adapter_name=source,
            target_adapter_name=target,
        )
    except (ETLIREngineError, UnknownAdapterError, OSError, ValueError) as error:
        typer.echo(f"ETLIR error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Status: {result.status.value}")
    typer.echo(f"Evidence: {result.output_directory / 'evidence.json'}")
    diagnostics = result.evidence["diagnostics"]
    if isinstance(diagnostics, list):
        for diagnostic in diagnostics:
            if isinstance(diagnostic, dict):
                typer.echo(
                    f"[{diagnostic.get('severity')}] {diagnostic.get('code')}: "
                    f"{diagnostic.get('message')}",
                    err=True,
                )
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


@app.command("adapters")
def list_adapters() -> None:
    """List installed source and target adapters."""

    registry = AdapterRegistry()
    typer.echo("Source Adapters: " + ", ".join(registry.source_names()))
    typer.echo("Target Adapters: " + ", ".join(registry.target_names()))


if __name__ == "__main__":  # pragma: no cover
    app()
