"""`etlir corpus`: digest verification, per-group conversion, totals, ranked blockers."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from etlir.cli import main
from etlir.corpus import evaluate_corpus, normalize_reason
from tests.conftest import CASES


def _manifest(tmp_path: Path, digest: str | None = None) -> tuple[Path, Path]:
    root = tmp_path / "external"
    group = root / "demo"
    group.mkdir(parents=True)
    src = CASES / "pc-mixed-blocked" / "source" / "wf_mixed.xml"
    shutil.copyfile(src, group / "wf_mixed.xml")
    sha = digest or hashlib.sha256(src.read_bytes()).hexdigest()
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        "schema_version = 1\n"
        '[[group]]\nid = "demo"\nlicense = "Apache-2.0"\n'
        f'files = [{{ path = "wf_mixed.xml", sha256 = "{sha}" }}]\n'
        '[[group]]\nid = "cases-only"\nfiles = []\n',
        "utf-8",
    )
    return manifest, root


def test_corpus_measures_verified_groups(tmp_path: Path) -> None:
    manifest, root = _manifest(tmp_path)
    result = evaluate_corpus(manifest, root, tmp_path / "out", targets=["duckdb"])
    cases, demo = result["groups"]  # sorted by id
    assert cases["group"] == "cases-only" and cases["status"] == "no-pinned-files"
    assert demo["status"] == "converted"
    assert demo["files_verified"] == {"count": 1, "of": 1}
    emitted = result["totals"]["targets"]["duckdb"]["dataflows_emitted"]
    assert 0 < emitted["count"] < emitted["of"]  # the case mixes clean and blocked dataflows
    reasons = [b["reason"] for b in result["blockers"]["unsupported_operations"]]
    assert "Sequence: cycling sequence generator" in reasons
    written = json.loads((tmp_path / "out" / "corpus.json").read_text("utf-8"))
    assert written == json.loads(json.dumps(result))
    assert str(tmp_path) not in (tmp_path / "out" / "corpus.json").read_text("utf-8")


def test_corpus_refuses_files_that_do_not_match_their_digest(tmp_path: Path) -> None:
    manifest, root = _manifest(tmp_path, digest="0" * 64)
    result = evaluate_corpus(manifest, root, tmp_path / "out")
    assert result["groups"][1]["status"] == "not-verified"
    assert result["totals"]["files_verified"] == {"count": 0, "of": 1}


def test_cli_corpus(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    manifest, root = _manifest(tmp_path)
    args = [
        "corpus",
        "--manifest",
        str(manifest),
        "--root",
        str(root),
        "--out",
        str(tmp_path / "o"),
    ]
    assert main([*args, "--target", "duckdb"]) == 0
    assert "files verified 1/1" in capsys.readouterr().out


def test_reasons_are_normalized_to_constructs() -> None:
    assert (
        normalize_reason("depends on variable v_A: variable port v_B is read before it is set")
        == "variable port <name> is read before it is set"
    )
    assert (
        normalize_reason("unconnected input ports ['A', 'B']")
        == "unconnected input ports [<names>]"
    )
    assert normalize_reason("function FIRST/2 is not in the supported subset").startswith(
        "function FIRST"
    )
