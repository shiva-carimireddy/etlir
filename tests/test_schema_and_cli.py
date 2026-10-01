from __future__ import annotations

import json
from pathlib import Path

import pytest

from etlir.canonical.model import CanonicalDocument
from etlir.cli import main
from etlir.serialization import canonical_schema, canonical_schema_filename, dumps

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas" / "canonical"


def test_committed_schema_matches_model() -> None:
    committed = SCHEMAS / canonical_schema_filename()
    assert committed.is_file(), "run: etlir schema --out schemas/canonical"
    assert json.loads(committed.read_text("utf-8")) == canonical_schema(), (
        "Canonical IR model changed without regenerating the schema. If the change is "
        "intended, bump IR_VERSION per docs/decisions/0004-versioning.md and regenerate."
    )


def test_validate_command(
    toy_doc: CanonicalDocument, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = tmp_path / "good.json"
    good.write_text(dumps(toy_doc), "utf-8")
    assert main(["validate", str(good)]) == 0
    assert json.loads(capsys.readouterr().out) == {"diagnostics": []}

    bad = tmp_path / "bad.json"
    bad.write_text(dumps(toy_doc.model_copy(update={"datasets": []})), "utf-8")
    assert main(["validate", str(bad)]) == 1


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert "canonical IR 0.2.0" in capsys.readouterr().out
