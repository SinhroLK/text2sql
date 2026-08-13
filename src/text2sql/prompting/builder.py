from __future__ import annotations

from text2sql.domain import SchemaSnapshot
from text2sql.schema import serialize_simple_schema

PROMPT_VERSION = "phase0-simple-schema-v1"


def build_baseline_prompt(question: str, schema: SchemaSnapshot) -> str:
    if not question.strip():
        raise ValueError("Question must not be empty")

    schema_text = serialize_simple_schema(schema)
    return (
        "You translate natural-language questions into one executable SQL query.\n"
        f"Use the {schema.dialect} dialect. Use only identifiers from the schema.\n"
        "Return SQL only, without Markdown or explanation.\n\n"
        f"Schema:\n{schema_text}\n\n"
        f"Question:\n{question}\n\nSQL:"
    )

