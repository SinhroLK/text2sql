from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Text2SQLExample:
    example_id: str
    db_id: str
    question: str
    dialect: str
    split: str
    gold_sql: str | None = None
    gold_result_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    data_type: str
    nullable: bool
    primary_key: bool
    ordinal_position: int = 0
    primary_key_position: int = 0
    default_sql: str | None = None

    def __post_init__(self) -> None:
        if self.primary_key and self.primary_key_position == 0:
            object.__setattr__(self, "primary_key_position", 1)
        if not self.name.strip() or not self.data_type.strip():
            raise ValueError("Column name and data_type must not be empty")
        if self.ordinal_position < 0 or self.primary_key_position < 0:
            raise ValueError("Column positions must be non-negative")
        if self.primary_key != (self.primary_key_position > 0):
            raise ValueError("primary_key must match primary_key_position")


@dataclass(frozen=True)
class ForeignKeySchema:
    source_column: str
    target_table: str
    target_column: str
    constraint_id: int = 0
    sequence: int = 0

    def __post_init__(self) -> None:
        if (
            not self.source_column.strip()
            or not self.target_table.strip()
            or not self.target_column.strip()
        ):
            raise ValueError("Foreign-key identifiers must not be empty")
        if self.constraint_id < 0 or self.sequence < 0:
            raise ValueError("Foreign-key positions must be non-negative")


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: tuple[ColumnSchema, ...]
    foreign_keys: tuple[ForeignKeySchema, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Table name must not be empty")
        names = [column.name.casefold() for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"Table {self.name!r} contains duplicate column names")
        positions = [column.ordinal_position for column in self.columns]
        if positions != list(range(len(self.columns))):
            raise ValueError(
                f"Table {self.name!r} columns are not in canonical ordinal order"
            )
        primary_key_positions = sorted(
            column.primary_key_position
            for column in self.columns
            if column.primary_key
        )
        if primary_key_positions != list(range(1, len(primary_key_positions) + 1)):
            raise ValueError(f"Table {self.name!r} primary-key positions are invalid")


@dataclass(frozen=True)
class SchemaSnapshot:
    db_id: str
    dialect: str
    tables: tuple[TableSchema, ...]

    def __post_init__(self) -> None:
        if not self.db_id.strip() or not self.dialect.strip():
            raise ValueError("Schema db_id and dialect must not be empty")
        table_names = [table.name.casefold() for table in self.tables]
        if len(table_names) != len(set(table_names)):
            raise ValueError("Schema contains duplicate table names")


@dataclass(frozen=True)
class GenerationInput:
    question: str
    prompt: str
    schema: SchemaSnapshot
    model_id: str


@dataclass(frozen=True)
class GenerationResult:
    run_id: str
    db_id: str
    question: str
    dialect: str
    provider: str
    model_id: str
    prompt_version: str
    prompt_hash: str
    schema_hash: str
    generated_sql: tuple[str, ...]
    selected_sql: str | None
    validation_status: str
    execution_status: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float | None = None
    error_category: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

