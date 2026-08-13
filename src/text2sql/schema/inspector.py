from __future__ import annotations

import sqlite3
from pathlib import Path

from text2sql.domain import (
    ColumnSchema,
    ForeignKeySchema,
    SchemaSnapshot,
    TableSchema,
)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def inspect_sqlite_schema(database_path: str | Path, db_id: str | None = None) -> SchemaSnapshot:
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {path}")

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        table_rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        tables: list[TableSchema] = []
        for (table_name,) in table_rows:
            quoted = _quote_identifier(table_name)
            column_rows = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            foreign_key_rows = connection.execute(f"PRAGMA foreign_key_list({quoted})").fetchall()

            columns = tuple(
                ColumnSchema(
                    name=row[1],
                    data_type=row[2] or "UNKNOWN",
                    nullable=not bool(row[3]),
                    primary_key=bool(row[5]),
                )
                for row in column_rows
            )
            foreign_keys = tuple(
                ForeignKeySchema(
                    source_column=row[3],
                    target_table=row[2],
                    target_column=row[4],
                )
                for row in foreign_key_rows
            )
            tables.append(TableSchema(table_name, columns, foreign_keys))

        return SchemaSnapshot(
            db_id=db_id or path.stem,
            dialect="sqlite",
            tables=tuple(tables),
        )
    finally:
        connection.close()

