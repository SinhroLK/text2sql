from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Collection, Mapping, TypeAlias

from text2sql.domain import SchemaSnapshot

from .canonical import validate_canonical_schema

MSCHEMA_VERSION = "xiyan-compatible-v1"
SampleValue: TypeAlias = str | int | float
MSchemaExamples: TypeAlias = Mapping[tuple[str, str], tuple[SampleValue, ...]]

_SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "secret",
    "accesstoken",
    "authtoken",
    "apikey",
    "accesskey",
    "privatekey",
    "credential",
    "token",
    "socialsecurity",
    "ssn",
    "email",
    "phone",
    "telephone",
    "mobile",
    "address",
    "streetaddress",
    "postaladdress",
    "dateofbirth",
    "birthdate",
    "birth",
    "creditcard",
    "card",
    "cardnumber",
    "cvv",
    "iban",
)


@dataclass(frozen=True)
class MSchemaSamplePolicy:
    examples_per_column: int = 3
    max_text_length: int = 50
    scan_rows_per_column: int = 24

    def __post_init__(self) -> None:
        if self.examples_per_column < 0:
            raise ValueError("examples_per_column must be non-negative")
        if self.max_text_length <= 0 or self.scan_rows_per_column <= 0:
            raise ValueError("M-Schema text and scan limits must be positive")
        if self.scan_rows_per_column < self.examples_per_column:
            raise ValueError("scan_rows_per_column must cover examples_per_column")


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _safe_identifier(identifier: str) -> str:
    if any(ord(character) < 32 for character in identifier):
        raise ValueError("M-Schema identifiers must not contain control characters")
    return identifier


def _is_sensitive_column(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return any(marker in normalized for marker in _SENSITIVE_MARKERS)


def _safe_sample(value: object, policy: MSchemaSamplePolicy) -> SampleValue | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if not isinstance(value, str) or len(value) > policy.max_text_length:
        return None
    return value


def sample_sqlite_mschema_values(
    database_path: str | Path,
    schema: SchemaSnapshot,
    policy: MSchemaSamplePolicy = MSchemaSamplePolicy(),
    *,
    columns: Collection[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], tuple[SampleValue, ...]]:
    validate_canonical_schema(schema)
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {path}")
    if policy.examples_per_column == 0:
        return {}

    real_columns = {
        (table.name, column.name)
        for table in schema.tables
        for column in table.columns
    }
    requested_columns = real_columns if columns is None else set(columns)
    unknown = sorted(requested_columns - real_columns)
    if unknown:
        raise ValueError(f"M-Schema sampling requested unknown columns: {unknown!r}")

    sampled: dict[tuple[str, str], tuple[SampleValue, ...]] = {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        for table in schema.tables:
            quoted_table = _quote_identifier(table.name)
            primary_key = sorted(
                (column for column in table.columns if column.primary_key),
                key=lambda column: column.primary_key_position,
            )
            order_by = ", ".join(
                _quote_identifier(column.name) for column in primary_key
            ) or "rowid"
            for column in table.columns:
                if (table.name, column.name) not in requested_columns:
                    continue
                if _is_sensitive_column(column.name):
                    continue
                quoted_column = _quote_identifier(column.name)
                rows = connection.execute(
                    f"SELECT {quoted_column} "
                    f"FROM {quoted_table} "
                    f"WHERE {quoted_column} IS NOT NULL "
                    f"ORDER BY {order_by} "
                    "LIMIT ?",
                    (policy.scan_rows_per_column,),
                ).fetchall()
                values: list[SampleValue] = []
                seen: set[tuple[type[object], SampleValue]] = set()
                for (raw_value,) in rows:
                    value = _safe_sample(raw_value, policy)
                    if value is None:
                        continue
                    identity = (type(value), value)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    values.append(value)
                    if len(values) == policy.examples_per_column:
                        break
                if values:
                    sampled[(table.name, column.name)] = tuple(values)
        return sampled
    finally:
        connection.close()


def _render_value(value: SampleValue) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"Unsupported M-Schema example value: {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("M-Schema example numbers must be finite")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def serialize_mschema(
    schema: SchemaSnapshot,
    examples: MSchemaExamples | None = None,
) -> str:
    validate_canonical_schema(schema)
    example_values = examples or {}
    real_columns = {
        (table.name, column.name)
        for table in schema.tables
        for column in table.columns
    }
    unknown = sorted(set(example_values) - real_columns)
    if unknown:
        raise ValueError(f"M-Schema examples contain unknown columns: {unknown!r}")
    sensitive = sorted(
        key for key in example_values if _is_sensitive_column(key[1])
    )
    if sensitive:
        raise ValueError(
            f"M-Schema examples contain sensitive columns: {sensitive!r}"
        )

    lines = [f"〖DB_ID〗 {_safe_identifier(schema.db_id)}", "〖Schema〗"]
    foreign_keys: list[str] = []
    for table in schema.tables:
        table_name = _safe_identifier(table.name)
        lines.extend((f"# Table: {table_name}", "["))
        fields: list[str] = []
        for column in table.columns:
            column_name = _safe_identifier(column.name)
            simple_type = column.data_type.split("(", 1)[0].strip().upper() or "UNKNOWN"
            parts = [f"{column_name}:{simple_type}"]
            if column.primary_key:
                parts.append("Primary Key")
            values = example_values.get((table.name, column.name), ())
            if values:
                rendered = ", ".join(_render_value(value) for value in values)
                parts.append(f"Examples: [{rendered}]")
            fields.append(f"({', '.join(parts)})")
        lines.append(",\n".join(fields))
        lines.append("]")
        for foreign_key in table.foreign_keys:
            foreign_keys.append(
                f"{table_name}.{_safe_identifier(foreign_key.source_column)}="
                f"{_safe_identifier(foreign_key.target_table)}."
                f"{_safe_identifier(foreign_key.target_column)}"
            )
    if foreign_keys:
        lines.append("〖Foreign keys〗")
        lines.extend(foreign_keys)
    return "\n".join(lines)


def mschema_sha256(
    schema: SchemaSnapshot,
    examples: MSchemaExamples | None = None,
) -> str:
    rendered = serialize_mschema(schema, examples)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
