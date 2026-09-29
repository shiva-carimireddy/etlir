"""Canonical IR: the source- and target-independent model at the center of ETLIR."""

from etlir.canonical.invariants import CODES, validate, walk_expression
from etlir.canonical.model import IR_VERSION, CanonicalDocument, SourceRef

__all__ = ["CODES", "IR_VERSION", "CanonicalDocument", "SourceRef", "validate", "walk_expression"]
