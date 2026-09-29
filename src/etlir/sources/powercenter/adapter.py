from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from etlir import __version__ as _etlir_version
from etlir.canonical.model import SourceRef
from etlir.contracts import InputArtifact, NormalizationResult, RawBundle, SourceAdapter
from etlir.evidence import Diagnostic, Severity
from etlir.serialization import sha256_file
from etlir.sources.powercenter import xmlio
from etlir.sources.powercenter.inventory import build_inventory
from etlir.sources.powercenter.normalize import ADAPTER_ID, Normalizer
from etlir.sources.powercenter.raw import OMISSIONS, to_raw
from etlir.sources.powercenter.symbols import SymbolTable

RAW_IR_VERSION = "0.1.0"


class PowerCenterAdapter(SourceAdapter):
    """Informatica PowerCenter XML exports (POWERMART) -> Canonical IR."""

    id = ADAPTER_ID
    version = _etlir_version
    ir_version = "0.1.0"
    description = "Informatica PowerCenter repository XML exports (documented subset)"

    def __init__(self, max_bytes: int = xmlio.MAX_BYTES) -> None:
        self.max_bytes = max_bytes

    def accepts(self, path: Path) -> bool:
        return path.is_file() and xmlio.sniff(path)

    def load(self, inputs: Sequence[Path], root: Path) -> RawBundle:
        files: list[dict[str, Any]] = []
        artifacts: list[InputArtifact] = []
        diagnostics: list[Diagnostic] = []
        for path in sorted(
            inputs, key=lambda p: p.resolve().relative_to(root.resolve()).as_posix()
        ):
            rel = path.resolve().relative_to(root.resolve()).as_posix()
            data = path.read_bytes()
            artifacts.append(InputArtifact(path=rel, sha256=sha256_file(path), size=len(data)))
            try:
                loaded = xmlio.load(data, self.max_bytes)
            except xmlio.LoadError as exc:
                diagnostics.append(
                    Diagnostic(
                        code=exc.code,
                        severity=Severity.ERROR,
                        message=str(exc),
                        subject_id=None,
                        source=SourceRef(adapter=self.id, artifact=rel, locator="/"),
                    )
                )
                continue
            files.append(
                {
                    "path": rel,
                    "encoding": loaded.encoding,
                    "doctype": loaded.doctype,
                    "root": to_raw(loaded.root),
                }
            )
        return RawBundle(
            adapter=self.id,
            adapter_version=self.version,
            raw_ir_version=RAW_IR_VERSION,
            inputs=artifacts,
            payload={"files": files},
            omissions=OMISSIONS,
            diagnostics=diagnostics,
        )

    @staticmethod
    def symbols(raw: RawBundle) -> SymbolTable:
        table = SymbolTable()
        for f in raw.payload["files"]:
            table.add_file(f["path"], f["root"])
        return table

    def inventory(self, raw: RawBundle) -> dict[str, Any]:
        rejected = [
            {"path": d.source.artifact if d.source else "", "code": d.code, "message": d.message}
            for d in raw.diagnostics
        ]
        return build_inventory(raw.payload["files"], rejected, self.symbols(raw))

    def normalize(self, raw: RawBundle) -> NormalizationResult:
        normalizer = Normalizer(self.symbols(raw))
        document = normalizer.run()
        return NormalizationResult(
            document=document,
            diagnostics=[*raw.diagnostics, *normalizer.diagnostics],
            evidence=normalizer.evidence,
        )
