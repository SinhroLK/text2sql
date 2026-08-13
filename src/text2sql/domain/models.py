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


@dataclass(frozen=True)
class ForeignKeySchema:
    source_column: str
    target_table: str
    target_column: str


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: tuple[ColumnSchema, ...]
    foreign_keys: tuple[ForeignKeySchema, ...] = ()


@dataclass(frozen=True)
class SchemaSnapshot:
    db_id: str
    dialect: str
    tables: tuple[TableSchema, ...]


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

