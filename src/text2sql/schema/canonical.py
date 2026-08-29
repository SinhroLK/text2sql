from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from text2sql.domain import SchemaSnapshot

CANONICAL_SCHEMA_VERSION = 1


def validate_canonical_schema(schema: SchemaSnapshot) -> None:
    """Validate ordering and every identifier relationship in a schema snapshot."""
    expected_tables = sorted(
        schema.tables, key=lambda table: (table.name.casefold(), table.name)
    )
    if list(schema.tables) != expected_tables:
        raise ValueError("Schema tables are not in canonical name order")

    tables = {table.name.casefold(): table for table in schema.tables}
    for table in schema.tables:
        columns = {column.name.casefold() for column in table.columns}
        expected_foreign_keys = sorted(
            table.foreign_keys,
            key=lambda foreign_key: (
                foreign_key.constraint_id,
                foreign_key.sequence,
                foreign_key.source_column.casefold(),
                foreign_key.target_table.casefold(),
                foreign_key.target_column.casefold(),
            ),
        )
        if list(table.foreign_keys) != expected_foreign_keys:
            raise ValueError(
                f"Table {table.name!r} foreign keys are not in canonical order"
            )
        for foreign_key in table.foreign_keys:
            if foreign_key.source_column.casefold() not in columns:
                raise ValueError(
                    "Foreign key source "
                    f"{table.name}.{foreign_key.source_column} does not exist"
                )
            target = tables.get(foreign_key.target_table.casefold())
            if target is None:
                raise ValueError(
                    f"Foreign key target table {foreign_key.target_table!r} does not exist"
                )
            target_columns = {column.name.casefold() for column in target.columns}
            if foreign_key.target_column.casefold() not in target_columns:
                raise ValueError(
                    "Foreign key target "
                    f"{foreign_key.target_table}.{foreign_key.target_column} does not exist"
                )


def canonical_schema_payload(schema: SchemaSnapshot) -> dict[str, object]:
    validate_canonical_schema(schema)
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "db_id": schema.db_id,
        "dialect": schema.dialect,
        "tables": [asdict(table) for table in schema.tables],
    }


def serialize_canonical_schema(schema: SchemaSnapshot) -> str:
    return json.dumps(
        canonical_schema_payload(schema),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_schema_sha256(schema: SchemaSnapshot) -> str:
    encoded = serialize_canonical_schema(schema).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
