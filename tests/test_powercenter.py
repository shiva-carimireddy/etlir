"""PowerCenter source adapter: secure loading, Raw IR preservation, resolution, rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from defusedxml.ElementTree import fromstring

from etlir.canonical.invariants import validate
from etlir.canonical.model import OpaqueNode, TaskKind
from etlir.evidence import EvidenceStatus
from etlir.serialization import dumps
from etlir.sources.powercenter import PowerCenterAdapter, xmlio
from etlir.sources.powercenter.raw import equivalent, from_raw
from etlir.testing import check_source_adapter
from tests.conftest import CASES, case_doc

ORDERS = CASES / "pc-orders" / "source"
MIXED = CASES / "pc-mixed-blocked" / "source"
HEAD = b'<?xml version="1.0" encoding="UTF-8"?>\n'


def _load(tmp_path: Path, body: bytes, name: str = "x.xml") -> list[str]:
    path = tmp_path / name
    path.write_bytes(body)
    adapter = PowerCenterAdapter()
    raw = adapter.load([path], tmp_path)
    return [d.code for d in raw.diagnostics]


# ------------------------------------------------------------------- secure loading


def test_external_entity_is_rejected(tmp_path: Path) -> None:
    body = HEAD + (
        b'<!DOCTYPE POWERMART [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        b"<POWERMART>&xxe;</POWERMART>"
    )
    assert _load(tmp_path, body) == ["PC-X-002"]


def test_entity_expansion_is_rejected(tmp_path: Path) -> None:
    body = HEAD + (
        b'<!DOCTYPE POWERMART [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b"<POWERMART>&b;</POWERMART>"
    )
    assert _load(tmp_path, body) == ["PC-X-002"]


def test_plain_doctype_is_accepted_without_loading_the_dtd(tmp_path: Path) -> None:
    body = HEAD + b'<!DOCTYPE POWERMART SYSTEM "powrmart.dtd">\n<POWERMART/>'
    assert _load(tmp_path, body) == []


def test_malformed_and_wrong_root(tmp_path: Path) -> None:
    assert _load(tmp_path, HEAD + b"<POWERMART><REPOSITORY></POWERMART>") == ["PC-X-001"]
    assert _load(tmp_path, HEAD + b"<OTHER/>") == ["PC-X-004"]


def test_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "big.xml"
    path.write_bytes(HEAD + b"<POWERMART>" + b" " * 2048 + b"</POWERMART>")
    raw = PowerCenterAdapter(max_bytes=1024).load([path], tmp_path)
    assert [d.code for d in raw.diagnostics] == ["PC-X-003"]


def test_sniff_is_content_based(tmp_path: Path) -> None:
    no_ext = tmp_path / "EXPORT"
    no_ext.write_bytes(HEAD + b'<!DOCTYPE POWERMART SYSTEM "powrmart.dtd">\n<POWERMART/>')
    doc = tmp_path / "notes.md"
    doc.write_text("An example: <POWERMART> ... </POWERMART>", "utf-8")
    assert xmlio.sniff(no_ext)
    assert not xmlio.sniff(doc)


# ------------------------------------------------------------------- Raw IR


@pytest.mark.parametrize(
    "path", sorted([*ORDERS.iterdir(), *MIXED.iterdir()]), ids=lambda p: p.name
)
def test_raw_ir_preserves_source_facts(path: Path) -> None:
    raw = PowerCenterAdapter().load([path], path.parent)
    rebuilt = from_raw(raw.payload["files"][0]["root"])
    assert equivalent(fromstring(path.read_bytes(), forbid_dtd=False), rebuilt)
    assert raw.inputs[0].sha256 and raw.omissions


def test_adapter_conforms() -> None:
    report = check_source_adapter(PowerCenterAdapter(), sorted(ORDERS.iterdir()), ORDERS)
    assert report.ok, report.failures


# ------------------------------------------------------------------- resolution


def test_cross_file_resolution() -> None:
    adapter = PowerCenterAdapter()
    raw = adapter.load(sorted(ORDERS.iterdir()), ORDERS)
    refs = adapter.inventory(raw)["references"]
    assert refs["session_to_mapping"]["resolved"] == refs["session_to_mapping"]["required"] == 3
    assert refs["instance_to_definition"]["unresolved"] == []


def test_missing_companion_export_is_reported_not_guessed() -> None:
    adapter = PowerCenterAdapter()
    result = adapter.normalize(adapter.load([ORDERS / "wf_orders.xml"], ORDERS))
    assert [d.code for d in result.diagnostics] == ["PC-R-001"] * 3
    tasks = result.document.pipelines[0].tasks
    assert {t.kind for t in tasks} == {TaskKind.UNSUPPORTED}
    missing = [e for e in result.evidence if e.status is EvidenceStatus.MISSING_INFORMATION]
    assert len(missing) == 3


# ------------------------------------------------------------------- normalization


def test_orders_normalizes_cleanly() -> None:
    adapter = PowerCenterAdapter()
    result = adapter.normalize(adapter.load(sorted(ORDERS.iterdir()), ORDERS))
    doc = result.document
    assert result.diagnostics == []
    assert validate(doc) == []
    kinds = sorted({o.spec.kind for df in doc.dataflows for o in df.operations})
    assert kinds == ["aggregate", "derive", "filter", "join", "project", "read", "route", "write"]
    assert all(not isinstance(e.ast, OpaqueNode) for df in doc.dataflows for e in df.expressions)
    [pipeline] = doc.pipelines
    deps = {
        t.name: [(d.task_id.split(":")[-1], d.condition.value) for d in t.depends_on]
        for t in pipeline.tasks
    }
    assert deps == {
        "s_m_LOAD_DIM_CUSTOMER": [],
        "s_m_LOAD_FACT_ORDERS": [("s_m_LOAD_DIM_CUSTOMER", "success")],
        "s_m_CUSTOMER_REVENUE": [("s_m_LOAD_FACT_ORDERS", "success")],
    }
    assert len({e.subject_id for e in result.evidence}) == len(result.evidence)


def test_router_join_and_parameter_rules(orders_doc) -> None:  # type: ignore[no-untyped-def]
    ops = {o.id.split(":")[-1]: o for df in orders_doc.dataflows for o in df.operations}
    route = ops["RTR_VALUE_BAND"]
    assert route.spec.kind == "route"
    assert route.spec.default_group == "DEFAULT1"
    assert [g.name for g in route.outputs] == ["HIGH_VALUE", "DEFAULT1"]
    join = ops["JNR_CUSTOMER_REVENUE"]
    assert join.spec.join_type == "left"  # master outer join keeps all detail rows
    [param] = orders_doc.parameters
    assert param.name == "$$HIGH_VALUE_THRESHOLD" and param.default == "100"


def test_every_entity_is_traced_to_the_source(orders_doc) -> None:  # type: ignore[no-untyped-def]
    refs = [p.source for p in orders_doc.pipelines]
    refs += [t.source for p in orders_doc.pipelines for t in p.tasks]
    refs += [df.source for df in orders_doc.dataflows]
    refs += [o.source for df in orders_doc.dataflows for o in df.operations]
    refs += [e.source for df in orders_doc.dataflows for e in df.expressions]
    refs += [d.source for d in orders_doc.datasets]
    assert all(
        r.adapter == "powercenter-xml" and r.locator.startswith("/POWERMART/") and r.rule
        for r in refs
    )


def test_unsupported_constructs_are_kept_with_reasons(mixed_doc) -> None:  # type: ignore[no-untyped-def]
    unsupported = {
        (df.name, o.id.split(":")[-1]): o.spec.reason
        for df in mixed_doc.dataflows
        for o in df.operations
        if o.spec.kind == "unsupported"
    }
    assert "'Sequence' is not supported" in unsupported[("s_m_CUSTOMER_KEYS", "SEQ_KEY")]
    assert "unsupported upstream ['SEQ_KEY']" in unsupported[("s_m_CUSTOMER_KEYS", "CUSTOMER_KEYS")]
    assert "SELECT * is not supported" in unsupported[("s_m_CUSTOMER_OVERRIDE", "SQ_CUSTOMERS")]
    assert "not row-aligned" in unsupported[("s_m_NOT_ALIGNED", "EXP_MIX")]
    opaque = [
        e.ast.reason
        for df in mixed_doc.dataflows
        for e in df.expressions
        if isinstance(e.ast, OpaqueNode)
    ]
    assert any("stateful" in (r or "") for r in opaque)
    links = [e for p in mixed_doc.pipelines for e in p.expressions]
    assert [e.original_text for e in links] == ["$s_m_CUSTOMER_OVERRIDE.ErrorCode = 0"]


def test_normalization_is_deterministic() -> None:
    a, b = case_doc("pc-orders"), case_doc("pc-orders")
    assert dumps(a) == dumps(b)
