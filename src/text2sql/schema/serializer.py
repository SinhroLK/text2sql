from __future__ import annotations

from text2sql.domain import SchemaSnapshot


def serialize_simple_schema(schema: SchemaSnapshot) -> str:
    lines = [f"Database: {schema.db_id}", f"Dialect: {schema.dialect}"]
    for table in schema.tables:
        lines.append(f"Table {table.name}:")
        for column in table.columns:
            flags: list[str] = []
            if column.primary_key:
                flags.append("PK")
            if not column.nullable:
                flags.append("NOT NULL")
            suffix = f" [{' '.join(flags)}]" if flags else ""
            lines.append(f"  - {column.name}: {column.data_type}{suffix}")
        for foreign_key in table.foreign_keys:
            lines.append(
                "  - FK "
                f"{foreign_key.source_column} -> "
                f"{foreign_key.target_table}.{foreign_key.target_column}"
            )
    return "\n".join(lines)

