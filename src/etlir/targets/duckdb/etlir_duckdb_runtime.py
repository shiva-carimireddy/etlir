"""ETLIR DuckDB runtime helpers (copied verbatim into every generated DuckDB package).

A job registers each input binding as a view, sets run-time parameters as DuckDB
variables, executes its static SQL file (one view per canonical operation), and writes
each target relation as JSON Lines. Depends only on the ``duckdb`` package.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
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


_JOB_START = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")


def _merge_keyed(
    con: duckdb.DuckDBPyConnection,
    relation: str,
    target: Path,
    mode: str,
    keys: list[str],
    provided: list[str],
) -> str:
    """Keyed update/upsert of an existing jsonl dataset (see WriteOp in the Canonical IR);
    returns a table holding the dataset's new content."""
    key_list = ", ".join(_q(k) for k in keys)
    dupes = con.execute(
        f"SELECT {key_list} FROM {_q(relation)} GROUP BY ALL HAVING count(*) > 1 LIMIT 1"
    ).fetchall()
    if dupes:
        raise ValueError(f"duplicate keys {keys} in rows written: {dupes[0]}")
    described = con.execute(f"DESCRIBE {_q(relation)}").fetchall()
    names = [str(d[0]) for d in described]
    if target.exists() and any(target.glob("*.json")):
        cols = "{" + ", ".join(f"{_lit(str(n))}: {_lit(str(t))}" for n, t, *_ in described) + "}"
        src = (
            f"read_json({_lit(str(target / '*.json'))}, columns={cols}, format='newline_delimited')"
        )
    else:
        src = f"(SELECT * FROM {_q(relation)} WHERE FALSE)"
    con.execute(f"CREATE TEMP TABLE __old AS SELECT * FROM {src}")
    on = " AND ".join(f"o.{_q(k)} = n.{_q(k)}" for k in keys)
    items = ", ".join(
        f"CASE WHEN n.__matched THEN n.{_q(c)} ELSE o.{_q(c)} END AS {_q(c)}"
        if c in provided and c not in keys
        else f"o.{_q(c)} AS {_q(c)}"
        for c in names
    )
    body = (
        f"SELECT {items} FROM __old AS o "
        f"LEFT JOIN (SELECT *, TRUE AS __matched FROM {_q(relation)}) AS n ON {on}"
    )
    if mode == "upsert":
        body += (
            f" UNION ALL SELECT * FROM {_q(relation)} AS n "
            f"WHERE NOT EXISTS (SELECT 1 FROM __old AS o WHERE {on})"
        )
    merged = f"__merged_{relation}"
    con.execute(f"CREATE TEMP TABLE {_q(merged)} AS {body}")
    con.execute("DROP TABLE __old")
    return merged


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
                pattern = str(path / "*.json") if path.is_dir() else str(path)
                src = f"read_json({_lit(pattern)}, columns={cols}, format='newline_delimited')"
            else:
                raise ValueError(f"unsupported input format '{fmt}' for binding '{binding_id}'")
            con.execute(f"CREATE TEMP VIEW {_q(binding_id)} AS SELECT * FROM {src}")
        for pid, (var, sql_type) in job["params"].items():
            if pid in params:
                value = params[pid]
            elif pid in job["defaults"]:
                value = job["defaults"][pid]
            elif job.get("builtins", {}).get(pid) == "run_start_time":
                value = os.environ.get("ETLIR_RUN_START_TIME") or _JOB_START
            else:
                raise KeyError(f"parameter '{pid}' has no value and no default")
            con.execute(f"SET VARIABLE {var} = CAST({_lit(str(value))} AS {sql_type})")
        sql = (Path(__file__).resolve().parent / job["sql"]).read_text("utf-8")
        con.execute(sql)
        for binding_id, relation, mode, *keyed in job["writes"]:
            b = bindings[binding_id]
            if b.get("format", "jsonl") != "jsonl":
                raise ValueError("the DuckDB runtime writes jsonl outputs only")
            target = _path(args.bindings, b)
            keys, provided = keyed if keyed else ([], [])
            if keys:
                relation = _merge_keyed(con, relation, target, mode, keys, provided)
                mode = "overwrite"
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
