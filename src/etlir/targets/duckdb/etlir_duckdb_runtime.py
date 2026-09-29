"""ETLIR DuckDB runtime helpers (copied verbatim into every generated DuckDB package).

A job registers each input binding as a view, sets run-time parameters as DuckDB
variables, executes its static SQL file (one view per canonical operation), and writes
each target relation as JSON Lines. Depends only on the ``duckdb`` package.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any

import duckdb

RUNTIME_VERSION = "1"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _path(bindings_file: Path, binding: dict[str, Any]) -> Path:
    p = Path(binding["path"])
    return p if p.is_absolute() else (bindings_file.parent / p).resolve()


def run(job: dict[str, Any]) -> None:
    parser = argparse.ArgumentParser(description=f"ETLIR DuckDB job for {job['dataflow_id']}")
    parser.add_argument("--bindings", required=True, type=Path)
    parser.add_argument("--params", type=Path)
    args = parser.parse_args()
    bindings = json.loads(args.bindings.read_text("utf-8"))
    params = json.loads(args.params.read_text("utf-8")) if args.params else {}
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET TimeZone = 'UTC'")
        for binding_id, columns in job["reads"].items():
            b = bindings[binding_id]
            fmt, path = b.get("format", "csv"), _path(args.bindings, b)
            cols = "{" + ", ".join(f"{_lit(n)}: {_lit(t)}" for n, t in columns) + "}"
            if fmt == "csv":
                opts = {"header": "true", "delim": "','", "quote": "'\"'", "escape": "'\"'"}
                o = b.get("options", {})
                if "sep" in o:
                    opts["delim"] = _lit(o["sep"])
                if str(o.get("header", "true")).lower() == "false":
                    opts["header"] = "false"
                src = (
                    f"read_csv({_lit(str(path))}, columns={cols}, auto_detect=false, "
                    f"nullstr='', allow_quoted_nulls=true, "
                    f"timestampformat='%Y-%m-%d %H:%M:%S', "
                    + ", ".join(f"{k}={v}" for k, v in opts.items())
                    + ")"
                )
            elif fmt == "jsonl":
                src = f"read_json({_lit(str(path))}, columns={cols}, format='newline_delimited')"
            else:
                raise ValueError(f"unsupported input format '{fmt}' for binding '{binding_id}'")
            con.execute(f"CREATE TEMP VIEW {_q(binding_id)} AS SELECT * FROM {src}")
        for pid, (var, sql_type) in job["params"].items():
            if pid in params:
                value = params[pid]
            elif pid in job["defaults"]:
                value = job["defaults"][pid]
            else:
                raise KeyError(f"parameter '{pid}' has no value and no default")
            con.execute(f"SET VARIABLE {var} = CAST({_lit(str(value))} AS {sql_type})")
        sql = (Path(__file__).resolve().parent / job["sql"]).read_text("utf-8")
        con.execute(sql)
        for binding_id, relation, mode in job["writes"]:
            b = bindings[binding_id]
            if b.get("format", "jsonl") != "jsonl":
                raise ValueError("the DuckDB runtime writes jsonl outputs only")
            target = _path(args.bindings, b)
            if target.exists():
                if mode == "error_if_exists":
                    raise FileExistsError(str(target))
                if mode == "overwrite":
                    shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
            part = target / f"part-{len(list(target.glob('part-*.json'))):05d}.json"
            # Decimals are written as strings so JSON never rounds them through a double.
            described = con.execute(f"DESCRIBE {_q(relation)}").fetchall()
            items = ", ".join(
                f"CAST({_q(name)} AS VARCHAR) AS {_q(name)}"
                if str(dtype).upper().startswith(("DECIMAL", "HUGEINT"))
                else _q(name)
                for name, dtype, *_ in described
            )
            con.execute(
                f"COPY (SELECT {items} FROM {_q(relation)}) TO {_lit(str(part))} (FORMAT json)"
            )
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        con.close()
    sys.exit(0)
