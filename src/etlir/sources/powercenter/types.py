"""PowerCenter datatype -> canonical DataType."""

from __future__ import annotations

from etlir.canonical.model import DataType, TypeKind


def _int(value: str) -> int | None:
    try:
        n = int(value)
    except ValueError:
        return None
    return n if n >= 0 else None


# Transformation (port) datatypes, as used in TRANSFORMFIELD.
_PORT = {
    "string": TypeKind.STRING,
    "nstring": TypeKind.STRING,
    "text": TypeKind.STRING,
    "ntext": TypeKind.STRING,
    "integer": TypeKind.INTEGER,
    "small integer": TypeKind.INTEGER,
    "bigint": TypeKind.BIGINT,
    "decimal": TypeKind.DECIMAL,
    "double": TypeKind.DOUBLE,
    "real": TypeKind.DOUBLE,
    "date/time": TypeKind.TIMESTAMP,
    "binary": TypeKind.BINARY,
}

# Native datatypes of source/target definitions (flat file and common databases).
_NATIVE = {
    **_PORT,
    "number": TypeKind.DECIMAL,
    "numeric": TypeKind.DECIMAL,
    "money": TypeKind.DECIMAL,
    "number(p,s)": TypeKind.DECIMAL,
    "datetime": TypeKind.TIMESTAMP,
    "date": TypeKind.TIMESTAMP,
    "timestamp": TypeKind.TIMESTAMP,
    "smalldatetime": TypeKind.TIMESTAMP,
    "datetime2": TypeKind.TIMESTAMP,
    "varchar": TypeKind.STRING,
    "varchar2": TypeKind.STRING,
    "nvarchar": TypeKind.STRING,
    "nvarchar2": TypeKind.STRING,
    "char": TypeKind.STRING,
    "nchar": TypeKind.STRING,
    "clob": TypeKind.STRING,
    "int": TypeKind.INTEGER,
    "smallint": TypeKind.INTEGER,
    "tinyint": TypeKind.INTEGER,
    "float": TypeKind.DOUBLE,
    "binary_double": TypeKind.DOUBLE,
    "binary_float": TypeKind.DOUBLE,
    "bit": TypeKind.INTEGER,
}


def _build(kind: TypeKind, precision: str, scale: str) -> DataType:
    p, s = _int(precision), _int(scale)
    if kind is TypeKind.DECIMAL:
        if p is None or p == 0 or p > 38:
            return DataType(kind=TypeKind.DECIMAL)
        return DataType(kind=kind, precision=p, scale=min(s or 0, p))
    if kind is TypeKind.STRING:
        return DataType(kind=kind, length=p or None)
    return DataType(kind=kind)


def port_type(datatype: str, precision: str = "", scale: str = "") -> DataType:
    kind = _PORT.get(datatype.strip().lower(), TypeKind.UNKNOWN)
    return _build(kind, precision, scale)


def native_type(datatype: str, precision: str = "", scale: str = "") -> DataType:
    kind = _NATIVE.get(datatype.strip().lower(), TypeKind.UNKNOWN)
    return _build(kind, precision, scale)
