"""Source-preserving Raw IR for PowerCenter XML.

Each element becomes ``{"tag", "attrs", "children"[, "text"]}``. Attribute values, element
order and non-whitespace text are kept exactly, including unknown elements.

Preservation guarantee (checked by :func:`equivalent` in tests over fixtures and the
public corpus): rebuilding XML from the Raw IR yields an element tree equal to the parsed
source in tags, attribute names and values, child order, and non-whitespace text.

Intentional omissions: comments, processing instructions, whitespace-only text and tails,
attribute order, and the lexical form of character references. The XML declaration's
encoding and the DOCTYPE are recorded separately.
"""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element

OMISSIONS = [
    "XML comments and processing instructions",
    "whitespace-only text and element tails",
    "attribute order (attributes are stored sorted by name)",
    "lexical form of character and entity references (values are stored decoded)",
]

RawNode = dict[str, Any]


def to_raw(el: Element) -> RawNode:
    node: RawNode = {
        "tag": el.tag,
        "attrs": dict(sorted(el.attrib.items())),
        "children": [to_raw(c) for c in el if isinstance(c.tag, str)],
    }
    if el.text is not None and el.text.strip():
        node["text"] = el.text
    return node


def from_raw(node: RawNode) -> Element:
    el = Element(node["tag"], node["attrs"])
    if "text" in node:
        el.text = node["text"]
    el.extend(from_raw(c) for c in node["children"])
    return el


def equivalent(a: Element, b: Element) -> bool:
    """Element trees equal up to the documented omissions."""

    def text(e: Element) -> str | None:
        return e.text if e.text is not None and e.text.strip() else None

    if a.tag != b.tag or a.attrib != b.attrib or text(a) != text(b):
        return False
    ac = [c for c in a if isinstance(c.tag, str)]
    bc = [c for c in b if isinstance(c.tag, str)]
    return len(ac) == len(bc) and all(equivalent(x, y) for x, y in zip(ac, bc, strict=True))
