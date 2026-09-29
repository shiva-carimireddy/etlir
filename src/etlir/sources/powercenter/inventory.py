"""Source inventory: counts with explicit units, computed before normalization.

Units:

* ``xml_elements``: number of XML elements with a given tag across all accepted files.
* ``distinct_definitions``: resolved folder-level definitions after merging identical
  duplicates (a definition repeated verbatim in two exports counts once).
* ``references``: required cross-object links found / resolved within the loaded group.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from etlir.sources.powercenter.symbols import KINDS, RNode, SymbolTable

KNOWN_TAGS = {
    "POWERMART",
    "REPOSITORY",
    "FOLDER",
    "SOURCE",
    "SOURCEFIELD",
    "TARGET",
    "TARGETFIELD",
    "TRANSFORMATION",
    "TRANSFORMFIELD",
    "TABLEATTRIBUTE",
    "INSTANCE",
    "CONNECTOR",
    "MAPPING",
    "MAPPLET",
    "MAPPINGVARIABLE",
    "TARGETLOADORDER",
    "GROUP",
    "ASSOCIATED_SOURCE_INSTANCE",
    "SESSION",
    "SESSTRANSFORMATIONINST",
    "SESSIONEXTENSION",
    "SESSIONCOMPONENT",
    "CONNECTIONREFERENCE",
    "CONFIG",
    "CONFIGREFERENCE",
    "ATTRIBUTE",
    "WORKFLOW",
    "WORKLET",
    "TASK",
    "TASKINSTANCE",
    "WORKFLOWLINK",
    "WORKFLOWVARIABLE",
    "SCHEDULER",
    "SCHEDULEINFO",
    "FLATFILE",
    "PARTITION",
    "FIELDDEPENDENCY",
    "METADATAEXTENSION",
    "ERPINFO",
    "VALUEPAIR",
    "INITPROP",
    "TRANSFORMFIELDATTR",
    "TRANSFORMFIELDATTRDEF",
    "SHORTCUT",
    "KEYWORD",
    "SCHEDULEOPTIONS",
    "STARTOPTIONS",
    "ENDOPTIONS",
    "RECURRING",
    "CUSTOM",
    "DAILYFREQUENCY",
    "REPEAT",
    "FILTER",
}


def _walk(node: dict[str, Any]) -> Any:
    yield node
    for c in node["children"]:
        yield from _walk(c)


def build_inventory(
    files: list[dict[str, Any]], rejected: list[dict[str, str]], table: SymbolTable
) -> dict[str, Any]:
    tags: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    ttypes: Counter[str] = Counter()
    task_types: Counter[str] = Counter()
    instance_types: Counter[str] = Counter()
    for f in files:
        for n in _walk(f["root"]):
            t = n["tag"]
            tags[t] += 1
            if t not in KNOWN_TAGS:
                unknown[t] += 1
            if t == "TRANSFORMATION":
                ttypes[n["attrs"].get("TYPE", "")] += 1
            elif t == "TASKINSTANCE":
                task_types[n["attrs"].get("TASKTYPE", "")] += 1
            elif t == "INSTANCE":
                instance_types[n["attrs"].get("TRANSFORMATION_TYPE", "")] += 1

    distinct: Counter[str] = Counter()
    conflicts: list[dict[str, str]] = []
    duplicates = 0
    kinds = ("session_to_mapping", "taskinstance_to_task", "instance_to_definition")
    required: Counter[str] = Counter()
    resolved: Counter[str] = Counter()
    unresolved: dict[str, list[str]] = {k: [] for k in kinds}

    def check(kind: str, ok: bool, what: str) -> None:
        required[kind] += 1
        if ok:
            resolved[kind] += 1
        else:
            unresolved[kind].append(what)

    for folder in table.sorted_folders():
        duplicates += folder.duplicates
        for kind in KINDS:
            distinct[kind] += len(folder.defs.get(kind, {}))
            for key in sorted(folder.conflicts.get(kind, set())):
                conflicts.append({"folder": folder.name, "kind": kind, "name": "/".join(key)})
        sessions: list[RNode] = [s for _, s in folder.items("SESSION")]
        for _, wf in folder.items("WORKFLOW"):
            sessions += list(wf.children("SESSION"))
            local = {c.name for c in wf.children() if c.tag in ("SESSION", "TASK", "WORKLET")}
            for ti in wf.children("TASKINSTANCE"):
                if ti.get("TASKTYPE") == "Start":
                    continue
                name = ti.get("TASKNAME")
                ok = name in local or any(
                    folder.get(k, name) is not None for k in ("SESSION", "TASK", "WORKLET")
                )
                check("taskinstance_to_task", ok, f"{folder.name}/{wf.name}/{ti.name}")
        for s in sessions:
            m = s.get("MAPPINGNAME")
            check(
                "session_to_mapping",
                folder.get("MAPPING", m) is not None,
                f"{folder.name}/{s.name} -> {m}",
            )
        for _, mapping in folder.items("MAPPING"):
            local_t = {t.name for t in mapping.children("TRANSFORMATION")}
            for inst in mapping.children("INSTANCE"):
                kind, name = inst.get("TYPE"), inst.get("TRANSFORMATION_NAME")
                if kind == "SOURCE":
                    ok = folder.get("SOURCE", inst.get("DBDNAME"), name) is not None
                elif kind == "TARGET":
                    ok = folder.get("TARGET", name) is not None
                elif kind == "MAPPLET":
                    ok = folder.get("MAPPLET", name) is not None
                elif inst.get("REUSABLE") == "YES":
                    ok = folder.get("TRANSFORMATION", name) is not None
                else:
                    ok = name in local_t
                check("instance_to_definition", ok, f"{folder.name}/{mapping.name}/{inst.name}")

    return {
        "units": {
            "xml_elements": "XML element count by tag across accepted files",
            "distinct_definitions": "folder-level definitions after merging identical "
            "duplicates; workflow-local sessions are counted under xml_elements only",
            "references": "required links discovered / resolved within the loaded group",
        },
        "files": {"accepted": len(files), "rejected": rejected},
        "folders": [f"{f.repository}/{f.name}" for f in table.sorted_folders()],
        "xml_elements": dict(sorted(tags.items())),
        "transformations_by_type": dict(sorted(ttypes.items())),
        "instances_by_type": dict(sorted(instance_types.items())),
        "task_instances_by_type": dict(sorted(task_types.items())),
        "unknown_tags": dict(sorted(unknown.items())),
        "distinct_definitions": dict(sorted(distinct.items())),
        "identical_duplicates_merged": duplicates,
        "conflicting_definitions": conflicts,
        "references": {
            k: {"required": required[k], "resolved": resolved[k], "unresolved": unresolved[k]}
            for k in kinds
        },
    }
