"""ETLIR Spark runtime helpers (copied verbatim into every generated Spark package).

Generated jobs depend only on PySpark and this module, not on ETLIR itself. The helpers
implement the canonical semantics that have no single PySpark built-in (NULL-safe concat,
substring positions) and the dataset I/O contract (bindings file, run-time parameters).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import decimal
import json
import os
import shutil
import sys
import traceback
from collections.abc import Callable
from functools import reduce
from pathlib import Path
from typing import Any

from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T
from pyspark.sql.window import Window

RUNTIME_VERSION = "1"


class Context:
    def __init__(self, bindings_path: Path, params_path: Path | None) -> None:
        self.bindings_path = bindings_path
        self.bindings: dict[str, Any] = json.loads(bindings_path.read_text("utf-8"))
        self.params: dict[str, Any] = (
            json.loads(params_path.read_text("utf-8")) if params_path else {}
        )
        self.defaults: dict[str, Any] = {}
        self.builtins: dict[str, str] = {}

    def binding(self, binding_id: str) -> dict[str, Any]:
        if binding_id not in self.bindings:
            raise KeyError(f"no binding for '{binding_id}' in {self.bindings_path}")
        b: dict[str, Any] = self.bindings[binding_id]
        return b

    def path(self, binding: dict[str, Any]) -> str:
        p = Path(binding["path"])
        if not p.is_absolute():
            p = (self.bindings_path.parent / p).resolve()
        return str(p)


# ---------------------------------------------------------------------- expressions


def col(name: str) -> Column:
    return F.col("`" + name.replace("`", "``") + "`")


def run_start_time() -> str:
    """The run's start instant: set once per run by the runner, else this job's start."""
    return os.environ.get("ETLIR_RUN_START_TIME") or _JOB_START


_JOB_START = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d %H:%M:%S")


def param(ctx: Context, parameter_id: str, spark_type: str | None) -> Column:
    if parameter_id in ctx.params:
        value = ctx.params[parameter_id]
    elif parameter_id in ctx.defaults:
        value = ctx.defaults[parameter_id]
    elif ctx.builtins.get(parameter_id) == "run_start_time":
        value = run_start_time()
    else:
        raise KeyError(f"parameter '{parameter_id}' has no value and no default")
    lit = F.lit(value)
    return lit.cast(spark_type) if spark_type else lit


def concat(*cols: Column) -> Column:
    """NULLs count as ''; NULL only when every argument is NULL."""
    all_null = reduce(lambda a, b: a & b, [c.isNull() for c in cols])
    joined = F.concat(*[F.coalesce(c, F.lit("")) for c in cols])
    return F.when(all_null, F.lit(None).cast("string")).otherwise(joined)


def substr(s: Column, start: Column, length: Column | None = None) -> Column:
    """1-based; 0 means 1; negative counts from the end; clamped to 1; length <= 0 -> ''."""
    n = F.length(s)
    pos = F.when(start > 0, start).when(start == 0, F.lit(1)).otherwise(n + start + 1)
    pos = F.greatest(pos, F.lit(1))
    ln = n if length is None else length
    any_null = s.isNull() | start.isNull() | ln.isNull()
    return (
        F.when(any_null, F.lit(None).cast("string"))
        .when(ln <= 0, F.lit(""))
        .otherwise(s.substr(pos, ln))
    )


_PUNCT = set(r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~""")
NUMBER_REGEX = r"^ *[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)? *$"
LEADING_NUMBER_REGEX = r"^ *([+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+))"
WHITESPACE_REGEX = r"^[ \t\n\r\f\x0B]+$"


def pad(s: Column, n: Column, fill: str, left: bool) -> Column:
    """Pad to n characters; a longer string is cut to its first n; n <= 0 gives ''."""
    padded = F.call_function("lpad" if left else "rpad", s, n, F.lit(fill))
    return (
        F.when(s.isNull() | n.isNull(), F.lit(None).cast("string"))
        .when(n <= 0, F.lit(""))
        .otherwise(padded)
    )


def instr(s: Column, sub: Column, start: int) -> Column:
    """1-based position of sub at or after start; 0 if absent or sub is empty."""
    found = F.call_function("locate", sub, s, F.lit(start))
    return F.when(s.isNull() | sub.isNull(), F.lit(None).cast("int")).otherwise(
        F.when(sub == "", F.lit(0)).otherwise(found)
    )


def replace(s: Column, old: str, new: str, ignore_case: bool) -> Column:
    """Replace every non-overlapping occurrence, left to right."""
    if not ignore_case:
        return F.call_function("replace", s, F.lit(old), F.lit(new))
    pattern = "(?i)" + "".join("\\" + c if c in _PUNCT else c for c in old)
    replacement = new.replace("\\", "\\\\").replace("$", "\\$")
    return F.regexp_replace(s, F.lit(pattern), F.lit(replacement))


def chr_ascii(n: Column) -> Column:
    return F.when((n >= 1) & (n <= 127), F.call_function("chr", n))


def matches_number(s: Column) -> Column:
    return s.rlike(NUMBER_REGEX)


def is_whitespace(s: Column) -> Column:
    return s.rlike(WHITESPACE_REGEX)


def leading_decimal(s: Column, scale: int) -> Column:
    """Value of the leading numeric prefix (0 if none), rounded half away from zero."""
    prefix = F.regexp_extract(s, LEADING_NUMBER_REGEX, 1)
    value = F.when(prefix == "", F.lit("0")).otherwise(prefix).cast("decimal(38,18)")
    return F.round(value, scale).cast(f"decimal(38,{scale})")


def parse_timestamp(s: Column, pattern: str, regex: str) -> Column:
    """Exact parse; a non-NULL string that does not match fails the job."""
    parsed = F.when(s.rlike(regex), F.try_to_timestamp(s, F.lit(pattern)))
    message = F.concat(F.lit(f"cannot parse timestamp with format {pattern}: "), s)
    return F.when(s.isNull(), F.lit(None).cast("timestamp")).otherwise(
        F.coalesce(parsed, F.raise_error(message))
    )


def can_parse_timestamp(s: Column, pattern: str, regex: str) -> Column:
    return F.when(s.isNull(), F.lit(None).cast("boolean")).otherwise(
        s.rlike(regex) & F.try_to_timestamp(s, F.lit(pattern)).isNotNull()
    )


def trunc_decimal(x: Column, places: int) -> Column:
    """Truncate toward zero to the given number of decimal places."""
    return F.when(x >= 0, F.floor(x, F.lit(places))).otherwise(F.ceil(x, F.lit(places)))


def lookup(
    inp: DataFrame, lk: DataFrame, on: Column, policy: str, order: list[str], name: str
) -> DataFrame:
    """Canonical lookup: left join ``inp`` to ``lk`` on ``on`` and apply the policy.

    ``any`` keeps the first match when lookup rows are sorted by ``order`` ascending with
    NULLs last; ``error`` fails if an input row has several matches; ``all`` keeps all.
    """
    if policy == "all":
        return inp.join(lk, on=on, how="left")
    rid, hit, rn = "__etlir_rid", "__etlir_hit", "__etlir_rn"
    left = inp.withColumn(rid, F.monotonically_increasing_id())
    joined = left.join(lk.withColumn(hit, F.lit(1)), on=on, how="left")
    if policy == "error":
        dup = joined.groupBy(rid).agg(F.count(hit).alias("n")).filter(F.col("n") > 1).limit(1)
        if dup.count():
            raise RuntimeError(f"lookup {name}: an input row has more than one match")
        return joined
    window = Window.partitionBy(rid).orderBy(*[col(c).asc_nulls_last() for c in order])
    return joined.withColumn(rn, F.row_number().over(window)).filter(F.col(rn) == 1)


# ---------------------------------------------------------------------- dataset I/O


def read(spark: SparkSession, ctx: Context, binding_id: str, schema: T.StructType) -> DataFrame:
    b = ctx.binding(binding_id)
    fmt, opts = b.get("format", "csv"), dict(b.get("options", {}))
    reader = spark.read.schema(schema).option("mode", "FAILFAST")
    if fmt == "csv":
        opts.setdefault("header", "true")
        opts.setdefault("timestampFormat", "yyyy-MM-dd HH:mm:ss")
        return reader.options(**opts).csv(ctx.path(b))
    if fmt == "jsonl":
        return reader.options(**opts).json(ctx.path(b))
    raise ValueError(f"unsupported input format '{fmt}' for binding '{binding_id}'")


_MODES = {"append": "append", "overwrite": "overwrite", "error_if_exists": "errorifexists"}


def write(ctx: Context, binding_id: str, df: DataFrame, mode: str) -> None:
    b = ctx.binding(binding_id)
    fmt = b.get("format", "jsonl")
    writer_kind = b.get("writer") or os.environ.get("ETLIR_SPARK_WRITER", "spark")
    path = ctx.path(b)
    if writer_kind == "driver":
        _driver_write(df, path, fmt, mode)
        return
    w = df.write.mode(_MODES[mode]).options(**b.get("options", {}))
    if fmt == "jsonl":
        w.option("ignoreNullFields", "false").option(
            "timestampFormat", "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
        ).json(path)
    elif fmt == "csv":
        w.option("header", "true").csv(path)
    else:
        raise ValueError(f"unsupported output format '{fmt}' for binding '{binding_id}'")


def write_keyed(
    spark: SparkSession,
    ctx: Context,
    binding_id: str,
    df: DataFrame,
    mode: str,
    keys: list[str],
    provided: list[str],
) -> None:
    """Keyed update/upsert of an existing jsonl dataset (see WriteOp in the Canonical IR)."""
    b = ctx.binding(binding_id)
    if b.get("format", "jsonl") != "jsonl":
        raise ValueError(f"keyed writes need a jsonl binding ('{binding_id}')")
    dupes = df.groupBy(*keys).count().filter(F.col("count") > 1).limit(1).collect()
    if dupes:
        raise ValueError(f"duplicate keys {keys} in rows written to '{binding_id}': {dupes[0]}")
    old = spark.createDataFrame(_read_jsonl_rows(Path(ctx.path(b)), df.schema), df.schema)
    new = df.withColumn("__matched", F.lit(True))
    on = reduce(lambda a, c: a & c, [old[k] == new[k] for k in keys])
    joined = old.join(new, on, "left")
    cols = [
        F.when(new["__matched"], new[c]).otherwise(old[c]).alias(c)
        if c in provided and c not in keys
        else old[c].alias(c)
        for c in df.columns
    ]
    result = joined.select(*cols)
    if mode == "upsert":
        result = result.unionByName(df.join(old, [df[k] == old[k] for k in keys], "left_anti"))
    # Materialize before the target is replaced.
    result = spark.createDataFrame(result.collect(), df.schema)
    write(ctx, binding_id, result, "overwrite")


def _read_jsonl_rows(path: Path, schema: T.StructType) -> list[tuple[Any, ...]]:
    """Rows of an existing jsonl dataset (absent: none), typed by ``schema``."""
    files = sorted(path.glob("*.json")) if path.is_dir() else [path] if path.exists() else []
    rows = []
    for f in files:
        for line in f.read_text("utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                rows.append(tuple(_typed(record.get(fl.name), fl.dataType) for fl in schema))
    return rows


def _typed(value: Any, t: T.DataType) -> Any:
    if value is None:
        return None
    if isinstance(t, T.DecimalType):
        return decimal.Decimal(str(value))
    if isinstance(t, T.TimestampType):
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if isinstance(t, T.DateType):
        return _dt.date.fromisoformat(str(value)[:10])
    if isinstance(t, (T.IntegerType, T.LongType, T.ShortType, T.ByteType)):
        return int(value)
    if isinstance(t, (T.DoubleType, T.FloatType)):
        return float(value)
    if isinstance(t, T.BooleanType):
        return value if isinstance(value, bool) else str(value).lower() == "true"
    return str(value) if not isinstance(value, str) else value


def _json_value(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def _driver_write(df: DataFrame, path: str, fmt: str, mode: str) -> None:
    """Write through the driver (for small local runs where Hadoop native I/O is missing,
    e.g. Windows without winutils). Same directory layout as Spark: <path>/part-*.json."""
    if fmt != "jsonl":
        raise ValueError("the driver writer supports jsonl only")
    target = Path(path)
    if target.exists():
        if mode == "error_if_exists":
            raise FileExistsError(path)
        if mode == "overwrite":
            shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    index = len(list(target.glob("part-*.json")))
    with (target / f"part-{index:05d}.json").open("w", encoding="utf-8", newline="\n") as fh:
        for row in df.toLocalIterator():
            record = {k: _json_value(v) for k, v in row.asDict().items()}
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------- entry point


def main(
    run: Callable[[SparkSession, Context], None],
    dataflow_id: str,
    defaults: dict[str, Any] | None = None,
    builtins: dict[str, str] | None = None,
) -> None:
    parser = argparse.ArgumentParser(description=f"ETLIR Spark job for {dataflow_id}")
    parser.add_argument("--bindings", required=True, type=Path)
    parser.add_argument("--params", type=Path)
    args = parser.parse_args()
    ctx = Context(args.bindings, args.params)
    ctx.defaults = dict(defaults or {})
    ctx.builtins = dict(builtins or {})
    builder = (
        SparkSession.builder.appName(f"etlir:{dataflow_id}")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.ui.enabled", "false")
    )
    if os.environ.get("ETLIR_SPARK_LAUNCHER") == "python":
        builder = builder.master(os.environ.get("ETLIR_SPARK_MASTER", "local[1]"))
    spark = builder.getOrCreate()
    code = 0
    try:
        run(spark, ctx)
    except Exception:  # report and fail the task
        traceback.print_exc()
        code = 1
    finally:
        info = os.environ.get("ETLIR_TASK_INFO")
        if info:
            conf = spark.sparkContext.getConf()
            Path(info).write_text(
                json.dumps(
                    {
                        "runtime_version": RUNTIME_VERSION,
                        "spark_version": spark.version,
                        "python_version": sys.version.split()[0],
                        "master": conf.get("spark.master"),
                        "sql_conf": {
                            k: spark.conf.get(k)
                            for k in ("spark.sql.session.timeZone", "spark.sql.ansi.enabled")
                        },
                        "writer": os.environ.get("ETLIR_SPARK_WRITER", "spark"),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                "utf-8",
            )
        spark.stop()
    sys.exit(code)
