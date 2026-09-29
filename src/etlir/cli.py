"""ETLIR command-line interface.

Only commands backed by implemented functionality are exposed. Pipeline commands
(inventory, convert, run, compare, report) are added as their stages land; see ROADMAP.md.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from etlir import __version__
from etlir.canonical.invariants import validate
from etlir.canonical.model import IR_VERSION, CanonicalDocument
from etlir.evidence import has_errors
from etlir.registry import Registry
from etlir.serialization import canonical_schema, canonical_schema_filename, dumps, write_json


def _cmd_version(_: argparse.Namespace) -> int:
    print(f"etlir {__version__} (canonical IR {IR_VERSION})")
    return 0


def _cmd_plugins(_: argparse.Namespace) -> int:
    reg = Registry.discover()
    print(
        dumps(
            {
                "sources": {k: v.version for k, v in sorted(reg.sources.items())},
                "targets": {k: v.version for k, v in sorted(reg.targets.items())},
                "errors": reg.errors,
            }
        ),
        end="",
    )
    return 1 if reg.errors else 0


def _cmd_schema(args: argparse.Namespace) -> int:
    out = Path(args.out) / canonical_schema_filename()
    write_json(out, canonical_schema())
    print(out)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        doc = CanonicalDocument.model_validate_json(Path(args.file).read_bytes())
    except ValidationError as exc:
        print(exc, file=sys.stderr)
        return 2
    diagnostics = validate(doc)
    print(dumps({"diagnostics": [d.model_dump(mode="json") for d in diagnostics]}), end="")
    return 1 if has_errors(diagnostics) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="etlir", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print tool and IR versions.").set_defaults(func=_cmd_version)
    sub.add_parser(
        "plugins", help="List installed source adapters and target emitters."
    ).set_defaults(func=_cmd_plugins)

    schema = sub.add_parser("schema", help="Export the Canonical IR JSON Schema.")
    schema.add_argument("--out", default="schemas/canonical")
    schema.set_defaults(func=_cmd_schema)

    val = sub.add_parser("validate", help="Validate a Canonical IR JSON document.")
    val.add_argument("file")
    val.set_defaults(func=_cmd_validate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
