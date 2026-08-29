from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

from text2sql.domain import GenerationInput, GenerationResult
from text2sql.prompting import (
    QUESTION_ONLY_PROMPT_VERSION,
    SIMPLE_SCHEMA_PROMPT_VERSION,
    build_baseline_prompt,
    build_question_only_prompt,
)
from text2sql.providers import SQLProvider
from text2sql.schema import inspect_sqlite_schema, serialize_simple_schema


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Text2SQLPipeline:
    def __init__(self, provider: SQLProvider) -> None:
        self.provider = provider

    def generate(
        self,
        question: str,
        database_path: str | Path,
        db_id: str | None = None,
        *,
        prompt_variant: str = "simple_schema",
    ) -> GenerationResult:
        schema = inspect_sqlite_schema(database_path, db_id=db_id)
        if prompt_variant == "question_only":
            prompt = build_question_only_prompt(question, schema.dialect)
            prompt_version = QUESTION_ONLY_PROMPT_VERSION
            schema_representation = "none"
        elif prompt_variant == "simple_schema":
            prompt = build_baseline_prompt(question, schema)
            prompt_version = SIMPLE_SCHEMA_PROMPT_VERSION
            schema_representation = "simple"
        else:
            raise ValueError(f"Unknown prompt variant: {prompt_variant!r}")
        generation_input = GenerationInput(
            question=question,
            prompt=prompt,
            schema=schema,
            model_id=self.provider.model_id,
        )

        started = time.perf_counter()
        response = self.provider.generate(generation_input)
        latency_ms = round((time.perf_counter() - started) * 1000)
        selected_sql = response.candidates[0] if response.candidates else None
        schema_text = serialize_simple_schema(schema)

        return GenerationResult(
            run_id=str(uuid.uuid4()),
            db_id=schema.db_id,
            question=question,
            dialect=schema.dialect,
            provider=self.provider.provider_name,
            model_id=self.provider.model_id,
            prompt_version=prompt_version,
            prompt_hash=_sha256(prompt),
            schema_hash=_sha256(schema_text),
            generated_sql=response.candidates,
            selected_sql=selected_sql,
            validation_status="not_implemented",
            execution_status="not_executed",
            latency_ms=latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            metadata={
                "phase": 2,
                "prompt_variant": prompt_variant,
                "schema_representation": schema_representation,
                "provider": response.metadata,
            },
        )

