"""Hardened loading of PowerCenter XML exports.

Real exports declare ``<!DOCTYPE POWERMART SYSTEM "powrmart.dtd">``, so a DOCTYPE is
accepted, but the DTD is never loaded, entity declarations are rejected (no expansion
attacks), external references are rejected (no XXE, no network), and input size is capped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

MAX_BYTES = 256 * 1024 * 1024

_DECL = re.compile(rb"^\s*<\?xml[^>]*encoding\s*=\s*[\"']([A-Za-z0-9._\-]+)[\"']", re.I)
_DOCTYPE = re.compile(rb"<!DOCTYPE\s+([^>\[]*)>", re.I)


class LoadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Loaded:
    root: Element
    encoding: str | None
    doctype: str | None


_ROOT = re.compile(
    rb"^(?:\xef\xbb\xbf)?\s*(?:<\?xml[^>]*>\s*)?(?:<!--.*?-->\s*)*(?:<!DOCTYPE[^>]*>\s*)?"
    rb"(?:<!--.*?-->\s*)*<POWERMART\b",
    re.S,
)


def sniff(path: Path) -> bool:
    """True if the file's root element is POWERMART (content-based: exports often lack an
    extension)."""
    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return _ROOT.match(head) is not None


def load(data: bytes, max_bytes: int = MAX_BYTES) -> Loaded:
    if len(data) > max_bytes:
        raise LoadError("PC-X-003", f"input exceeds {max_bytes} bytes")
    try:
        root = fromstring(data, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise LoadError("PC-X-002", f"forbidden XML construct: {type(exc).__name__}") from exc
    except ParseError as exc:
        raise LoadError("PC-X-001", f"malformed XML: {exc}") from exc
    if root.tag != "POWERMART":
        raise LoadError("PC-X-004", f"root element is <{root.tag}>, expected <POWERMART>")
    decl = _DECL.match(data[:512])
    doctype = _DOCTYPE.search(data[:4096])
    return Loaded(
        root=root,
        encoding=decl.group(1).decode("ascii") if decl else None,
        doctype=doctype.group(1).decode("latin-1").strip() if doctype else None,
    )
