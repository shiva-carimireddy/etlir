"""Reference workflow runner.

Executes a target package's ``workflow_plan.json``: tasks run serially in a
deterministic dependency order; a task runs only when its dependency conditions hold
(``success``, ``failure``, ``completion``); blocked tasks never run. By default a pipeline
with any blocked task is refused as a whole (fail closed); ``allow_partial`` runs the
tasks that are not blocked (blocking already covers everything downstream of a blocked
task). This is a reference runner, not a scheduler: no calendars, retries, events or
recovery.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from etlir.serialization import write_json


def spark_submit_path() -> str | None:
    override = os.environ.get("SPARK_SUBMIT")
    if override:
        return override
    exe = "spark-submit.cmd" if os.name == "nt" else "spark-submit"
    try:
        import pyspark

        candidate = Path(pyspark.__file__).parent / "bin" / exe
        if candidate.exists():
            return str(candidate)
    except ImportError:
        pass
    return shutil.which(exe)


def default_spark_writer() -> str:
    # Spark's own writer needs Hadoop native I/O (winutils) on Windows.
    return "driver" if os.name == "nt" else "spark"


def _argv(
    cmd: Mapping[str, Any], package: Path, bindings: Path, params: Path | None, launcher: str
) -> tuple[list[str], dict[str, str]]:
    script = str(package / cmd["script"])
    tail = ["--bindings", str(bindings)] + (["--params", str(params)] if params else [])
    env: dict[str, str] = {}
    if cmd["kind"] == "python":
        return [sys.executable, script, *tail], env
    if cmd["kind"] == "spark-submit":
        submit = spark_submit_path() if launcher in ("auto", "spark-submit") else None
        if launcher == "spark-submit" and submit is None:
            raise RuntimeError("spark-submit not found (install pyspark or set SPARK_SUBMIT)")
        env["PYSPARK_PYTHON"] = sys.executable
        env["PYSPARK_DRIVER_PYTHON"] = sys.executable
        if submit is None:
            env["ETLIR_SPARK_LAUNCHER"] = "python"
            env["ETLIR_SPARK_MASTER"] = cmd.get("master", "local[1]")
            return [sys.executable, script, *tail], env
        conf = [x for k, v in sorted(cmd.get("conf", {}).items()) for x in ("--conf", f"{k}={v}")]
        return [submit, "--master", cmd.get("master", "local[1]"), *conf, script, *tail], env
    raise ValueError(f"unknown command kind {cmd['kind']!r}")


def _order(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {t["id"]: t for t in tasks}
    done: set[str] = set()
    out: list[dict[str, Any]] = []
    pending = list(tasks)
    while pending:
        progressed = False
        for t in list(pending):
            if all(d["task_id"] in done or d["task_id"] not in by_id for d in t["depends_on"]):
                out.append(t)
                done.add(t["id"])
                pending.remove(t)
                progressed = True
        if not progressed:
            raise ValueError("task dependency cycle in workflow plan")
    return out


def _satisfied(dep: Mapping[str, str], status: Mapping[str, str]) -> bool:
    s = status.get(dep["task_id"])
    return (
        (dep["condition"] == "success" and s == "succeeded")
        or (dep["condition"] == "failure" and s == "failed")
        or (dep["condition"] == "completion" and s in ("succeeded", "failed"))
    )


def run_package(
    package: Path,
    bindings: Path,
    run_dir: Path,
    params: Path | None = None,
    *,
    launcher: str = "auto",
    spark_writer: str | None = None,
    allow_partial: bool = False,
    pipelines: list[str] | None = None,
) -> dict[str, Any]:
    plan = json.loads((package / "workflow_plan.json").read_text("utf-8"))
    run_dir.mkdir(parents=True, exist_ok=True)
    logs = run_dir / "logs"
    logs.mkdir(exist_ok=True)
    writer = spark_writer or default_spark_writer()
    results: list[dict[str, Any]] = []
    for p in plan["pipelines"]:
        if pipelines and p["id"] not in pipelines and p["name"] not in pipelines:
            continue
        blocked = [t["id"] for t in p["tasks"] if t["status"] == "blocked"]
        entry: dict[str, Any] = {"id": p["id"], "name": p["name"], "tasks": []}
        if blocked and not allow_partial:
            entry["status"] = "refused"
            entry["reason"] = (
                f"{len(blocked)} blocked task(s); rerun with --allow-partial "
                "to run the unaffected tasks"
            )
            entry["tasks"] = [
                {"id": t["id"], "status": "not_run" if t["status"] != "blocked" else "blocked"}
                for t in p["tasks"]
            ]
            results.append(entry)
            continue
        status: dict[str, str] = {}
        for t in _order(p["tasks"]):
            rec: dict[str, Any] = {"id": t["id"], "name": t["name"]}
            if t["status"] == "blocked":
                status[t["id"]] = rec["status"] = "blocked"
            elif not all(_satisfied(d, status) for d in t["depends_on"]):
                status[t["id"]] = rec["status"] = "not_triggered"
            elif t["command"] is None:
                status[t["id"]] = rec["status"] = "blocked"
                rec["reason"] = "no executable command"
            else:
                rec.update(_execute(t, package, bindings, params, run_dir, logs, launcher, writer))
                status[t["id"]] = rec["status"]
            entry["tasks"].append(rec)
        states = [r["status"] for r in entry["tasks"]]
        if "failed" in states:
            entry["status"] = "failed"
        elif states and all(x == "succeeded" for x in states):
            entry["status"] = "succeeded"
        elif "succeeded" in states:
            entry["status"] = "partial"
        else:
            entry["status"] = "not_run"
        results.append(entry)
    execution = {
        "runner": "etlir-reference-runner",
        "launcher": launcher,
        "spark_writer": writer,
        "allow_partial": allow_partial,
        "platform": {"os": os.name, "python": sys.version.split()[0]},
        "pipelines": results,
        "status": _overall([r["status"] for r in results]),
    }
    write_json(run_dir / "execution.json", execution)
    return execution


def _overall(states: list[str]) -> str:
    """succeeded: everything ran and succeeded; partial: nothing failed but some pipelines
    ran only their unblocked tasks; failed: anything failed, was refused, or nothing ran."""
    if states and all(x == "succeeded" for x in states):
        return "succeeded"
    if states and all(x in ("succeeded", "partial") for x in states):
        return "partial"
    return "failed"


def _execute(
    task: Mapping[str, Any],
    package: Path,
    bindings: Path,
    params: Path | None,
    run_dir: Path,
    logs: Path,
    launcher: str,
    writer: str,
) -> dict[str, Any]:
    safe = task["name"].replace("/", "_")
    info = run_dir / "tasks" / f"{safe}.runtime.json"
    info.parent.mkdir(parents=True, exist_ok=True)
    argv, extra_env = _argv(task["command"], package, bindings, params, launcher)
    env = {**os.environ, **extra_env, "ETLIR_SPARK_WRITER": writer, "ETLIR_TASK_INFO": str(info)}
    started = dt.datetime.now(dt.UTC)
    t0 = time.perf_counter()
    with (logs / f"{safe}.log").open("w", encoding="utf-8") as log:
        # argv comes from the package's own workflow plan (never from source artifacts).
        proc = subprocess.run(  # noqa: S603
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
        )
    rec: dict[str, Any] = {
        "status": "succeeded" if proc.returncode == 0 else "failed",
        "return_code": proc.returncode,
        "started_at": started.isoformat(timespec="seconds"),
        "duration_s": round(time.perf_counter() - t0, 3),
        "log": f"logs/{safe}.log",
        "launcher": Path(argv[0]).name,
    }
    if info.exists():
        rec["runtime"] = json.loads(info.read_text("utf-8"))
    return rec
