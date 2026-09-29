"""Conformance kit for source adapters and target emitters.

Plugin authors call these from their own test suites::

    from etlir.testing import check_source_adapter

    def test_conforms(tmp_path):
        report = check_source_adapter(MyAdapter(), inputs=[...], root=...)
        assert report.ok, report.failures

Passing the kit shows that a plugin honors the ETLIR contracts (determinism, provenance,
schema validity, non-mutation, fail-closed capabilities). It says nothing about semantic
correctness of the translation, which each plugin must test separately.
"""

from etlir.testing.conformance import (
    ConformanceReport,
    check_source_adapter,
    check_target_emitter,
)

__all__ = ["ConformanceReport", "check_source_adapter", "check_target_emitter"]
