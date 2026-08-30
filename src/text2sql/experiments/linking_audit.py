from __future__ import annotations

import hashlib
import math
from dataclasses import asdict
from typing import Any

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.evaluation import Spider2SQLiteDatabaseResolver
from text2sql.prompting import (
    LINKED_MSCHEMA_PROMPT_VERSION,
    MSCHEMA_PROMPT_VERSION,
    RECALL_LINKED_MSCHEMA_PROMPT_VERSION,
    build_linked_mschema_prompt,
    build_mschema_prompt,
    build_recall_linked_mschema_prompt,
)
from text2sql.schema import (
    SCHEMA_LINKER_VERSION,
    MSchemaExamples,
    MSchemaSamplePolicy,
    SchemaLinkingPolicy,
    canonical_schema_sha256,
    inspect_sqlite_schema,
    link_schema,
    sample_sqlite_mschema_values,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _percentile(values: list[int], probability: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def _reduction(original: int, selected: int) -> float:
    return 0.0 if original == 0 else 1.0 - selected / original


def audit_development_schema_linking(
    *,
    dataset: LoadedSpider2LiteDataset,
    database_resolver: Spider2SQLiteDatabaseResolver,
    sample_policy: MSchemaSamplePolicy,
    linking_policy: SchemaLinkingPolicy,
    prompt_variant: str = "linked_mschema",
) -> dict[str, Any]:
    """Audit linked schema/prompt reduction without provider or test access."""

    examples = tuple(
        sorted(
            dataset.for_split("development"),
            key=lambda item: item.example_id,
        )
    )
    if not examples:
        raise ValueError("Development split is empty")
    cache: dict[str, tuple[Any, MSchemaExamples]] = {}
    records: list[dict[str, Any]] = []

    for example in examples:
        database = database_resolver.resolve(example.db_id)
        cached = cache.get(example.db_id)
        if cached is None:
            schema = inspect_sqlite_schema(
                database.path, db_id=example.db_id
            )
            sampled = sample_sqlite_mschema_values(
                database.path, schema, sample_policy
            )
            cache[example.db_id] = (schema, sampled)
        else:
            schema, sampled = cached

        linked = link_schema(
            example.question,
            schema,
            sampled,
            linking_policy,
        )
        selected_columns = {
            (table.name, column.name)
            for table in linked.schema.tables
            for column in table.columns
        }
        linked_examples = {
            key: values
            for key, values in sampled.items()
            if key in selected_columns
        }
        full_prompt = build_mschema_prompt(
            example.question, schema, sampled
        )
        if prompt_variant == "linked_mschema":
            linked_prompt = build_linked_mschema_prompt(
                example.question, linked.schema, linked_examples
            )
            linked_prompt_version = LINKED_MSCHEMA_PROMPT_VERSION
        elif prompt_variant == "hybrid_linked_mschema":
            linked_prompt = build_recall_linked_mschema_prompt(
                example.question, schema, linked.schema, linked_examples
            )
            linked_prompt_version = (
                RECALL_LINKED_MSCHEMA_PROMPT_VERSION
            )
        else:
            raise ValueError(
                f"Unsupported schema-linking prompt variant: {prompt_variant!r}"
            )
        link_audit = linked.to_dict()
        records.append(
            {
                "example_id": example.example_id,
                "db_id": example.db_id,
                "full_schema_hash": canonical_schema_sha256(schema),
                "linked_schema_hash": canonical_schema_sha256(
                    linked.schema
                ),
                "full_prompt_version": MSCHEMA_PROMPT_VERSION,
                "linked_prompt_version": linked_prompt_version,
                "full_prompt_hash": _sha256(full_prompt),
                "linked_prompt_hash": _sha256(linked_prompt),
                "full_prompt_characters": len(full_prompt),
                "linked_prompt_characters": len(linked_prompt),
                "full_prompt_whitespace_tokens": len(
                    full_prompt.split()
                ),
                "linked_prompt_whitespace_tokens": len(
                    linked_prompt.split()
                ),
                "schema_linking": link_audit,
            }
        )

    full_chars = sum(
        int(item["full_prompt_characters"]) for item in records
    )
    linked_chars = sum(
        int(item["linked_prompt_characters"]) for item in records
    )
    full_words = sum(
        int(item["full_prompt_whitespace_tokens"]) for item in records
    )
    linked_words = sum(
        int(item["linked_prompt_whitespace_tokens"]) for item in records
    )
    original_tables = sum(
        int(item["schema_linking"]["original_table_count"])
        for item in records
    )
    selected_tables = sum(
        int(item["schema_linking"]["selected_table_count"])
        for item in records
    )
    original_columns = sum(
        int(item["schema_linking"]["original_column_count"])
        for item in records
    )
    selected_columns_total = sum(
        int(item["schema_linking"]["selected_column_count"])
        for item in records
    )
    table_counts = [
        int(item["schema_linking"]["selected_table_count"])
        for item in records
    ]
    column_counts = [
        int(item["schema_linking"]["selected_column_count"])
        for item in records
    ]
    return {
        "schema_version": 1,
        "scope": "development",
        "schema_linker_version": SCHEMA_LINKER_VERSION,
        "sample_policy": asdict(sample_policy),
        "linking_policy": asdict(linking_policy),
        "summary": {
            "total": len(records),
            "database_count": len(
                {item["db_id"] for item in records}
            ),
            "fallback_count": sum(
                bool(item["schema_linking"]["fallback_used"])
                for item in records
            ),
            "original_table_count_total": original_tables,
            "selected_table_count_total": selected_tables,
            "table_reduction_ratio": _reduction(
                original_tables, selected_tables
            ),
            "selected_tables_p50": _percentile(
                table_counts, 0.50
            ),
            "selected_tables_p95": _percentile(
                table_counts, 0.95
            ),
            "original_column_count_total": original_columns,
            "selected_column_count_total": selected_columns_total,
            "column_reduction_ratio": _reduction(
                original_columns, selected_columns_total
            ),
            "selected_columns_p50": _percentile(
                column_counts, 0.50
            ),
            "selected_columns_p95": _percentile(
                column_counts, 0.95
            ),
            "full_prompt_characters_total": full_chars,
            "linked_prompt_characters_total": linked_chars,
            "prompt_character_reduction_ratio": _reduction(
                full_chars, linked_chars
            ),
            "full_prompt_whitespace_tokens_total": full_words,
            "linked_prompt_whitespace_tokens_total": linked_words,
            "prompt_whitespace_token_reduction_ratio": _reduction(
                full_words, linked_words
            ),
        },
        "records": records,
    }
