"""Repository-level symbol table over all files of one corpus group.

Cross-file references (for example a session's MAPPINGNAME defined in another export) are
resolved against the whole group, never per file. Identical duplicate definitions are
merged; differing definitions under the same key are conflicts and resolve to nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


class RNode:
    """Read-only view of a Raw IR node with its artifact and XPath-like locator."""

    __slots__ = ("_raw", "artifact", "locator", "parent")

    def __init__(self, raw: dict[str, Any], artifact: str, locator: str, parent: RNode | None):
        self._raw = raw
        self.artifact = artifact
        self.locator = locator
        self.parent = parent

    @property
    def tag(self) -> str:
        tag: str = self._raw["tag"]
        return tag

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw

    def get(self, name: str, default: str = "") -> str:
        value: str = self._raw["attrs"].get(name, default)
        return value

    @property
    def name(self) -> str:
        return self.get("NAME")

    def children(self, tag: str | None = None) -> Iterator[RNode]:
        kids = self._raw["children"]
        counts: dict[str, int] = {}
        names: dict[tuple[str, str], int] = {}
        for k in kids:
            counts[k["tag"]] = counts.get(k["tag"], 0) + 1
            key = (k["tag"], k["attrs"].get("NAME", ""))
            names[key] = names.get(key, 0) + 1
        index: dict[str, int] = {}
        for k in kids:
            t = k["tag"]
            index[t] = index.get(t, 0) + 1
            if tag is not None and t != tag:
                continue
            nm = k["attrs"].get("NAME")
            if nm is not None and names[(t, nm)] == 1:
                step = f"{t}[@NAME={_quote(nm)}]"
            elif counts[t] == 1:
                step = t
            else:
                step = f"{t}[{index[t]}]"
            yield RNode(k, self.artifact, f"{self.locator}/{step}", self)

    def child(self, tag: str) -> RNode | None:
        return next(self.children(tag), None)

    def attributes(self, tag: str = "ATTRIBUTE") -> dict[str, str]:
        """NAME -> VALUE of ATTRIBUTE/TABLEATTRIBUTE children (last one wins)."""
        return {c.get("NAME"): c.get("VALUE") for c in self.children(tag)}

    def digest(self) -> str:
        blob = json.dumps(self._raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


def _quote(value: str) -> str:
    return "'" + value.replace("'", "&apos;") + "'" if "'" in value else f"'{value}'"


# ------------------------------------------------------------------------ identifiers

_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def make_id(prefix: str, *parts: str, parent: str | None = None) -> str:
    """Deterministic canonical identifier from native names.

    ``parent`` (an id made by this function) is nested without its prefix. Unsafe
    characters in ``parts`` are replaced; if anything was replaced, or the id is too long,
    a short digest of the exact inputs is appended so distinct names cannot collide.
    """
    clean = [_SAFE.sub("_", p) for p in parts]
    head = [prefix] + ([parent.split(":", 1)[1]] if parent else [])
    ident = ":".join([*head, *clean])
    if clean != list(parts) or len(ident) > 200:
        digest = hashlib.sha256("".join([parent or "", *parts]).encode("utf-8")).hexdigest()
        ident = f"{ident[:190]}.h{digest[:8]}"
    return ident


# ------------------------------------------------------------------------ symbol table

KINDS = (
    "SOURCE",
    "TARGET",
    "TRANSFORMATION",
    "MAPPLET",
    "MAPPING",
    "SESSION",
    "TASK",
    "WORKFLOW",
    "WORKLET",
    "CONFIG",
)


@dataclass
class Folder:
    repository: str
    name: str
    defs: dict[str, dict[tuple[str, ...], RNode]] = field(default_factory=dict)
    conflicts: dict[str, set[tuple[str, ...]]] = field(default_factory=dict)
    duplicates: int = 0

    def get(self, kind: str, *key: str) -> RNode | None:
        return self.defs.get(kind, {}).get(key)

    def is_conflict(self, kind: str, *key: str) -> bool:
        return key in self.conflicts.get(kind, set())

    def items(self, kind: str) -> list[tuple[tuple[str, ...], RNode]]:
        return sorted(self.defs.get(kind, {}).items())


def def_key(node: RNode) -> tuple[str, ...]:
    if node.tag == "SOURCE":
        return (node.get("DBDNAME"), node.name)
    return (node.name,)


class SymbolTable:
    def __init__(self) -> None:
        self.folders: dict[tuple[str, str], Folder] = {}

    def add_file(self, artifact: str, root: dict[str, Any]) -> None:
        top = RNode(root, artifact, "/POWERMART", None)
        for repo in top.children("REPOSITORY"):
            for folder_node in repo.children("FOLDER"):
                key = (repo.name, folder_node.name)
                folder = self.folders.setdefault(key, Folder(repo.name, folder_node.name))
                for child in folder_node.children():
                    if child.tag in KINDS and (
                        child.tag != "TRANSFORMATION" or child.get("REUSABLE") == "YES"
                    ):
                        self._add(folder, child.tag, def_key(child), child)

    @staticmethod
    def _add(folder: Folder, kind: str, key: tuple[str, ...], node: RNode) -> None:
        bucket = folder.defs.setdefault(kind, {})
        existing = bucket.get(key)
        if existing is None:
            if key not in folder.conflicts.get(kind, set()):
                bucket[key] = node
            return
        if existing.digest() == node.digest():
            folder.duplicates += 1
            return
        del bucket[key]
        folder.conflicts.setdefault(kind, set()).add(key)

    def sorted_folders(self) -> list[Folder]:
        return [self.folders[k] for k in sorted(self.folders)]
