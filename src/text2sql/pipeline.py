from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

from text2sql.domain import GenerationInput, GenerationResult
from text2sql.prompting import (
    MSCHEMA_PROMPT_VERSION,
    QUESTION_ONLY_PROMPT_VERSION,
    SIMPLE_SCHEMA_PROMPT_VERSION,
    build_baseline_prompt,
    build_mschema_prompt,
    build_question_only_prompt,
)
from text2sql.providers import SQLProvider
from text2sql.schema import (
    MSCHEMA_VERSION,
    MSchemaExamples,
    MSchemaSamplePolicy,
    canonical_schema_sha256,
    inspect_sqlite_schema,
    sample_sqlite_mschema_values,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Text2SQLPipeline:
    def __init__(self, provider: SQLProvider) -> None:
        self.provider = provider
        self._mschema_cache: dict[
            tuple[Path, str, MSchemaSamplePolicy], MSchemaExamples
        ] = {}

    def generate(
        self,
        question: str,
        database_path: str | Path,
        db_id: str | None = None,
        *,
        prompt_variant: str = "simple_schema",
        mschema_sample_policy: MSchemaSamplePolicy | None = None,
    ) -> GenerationResult:
        schema = inspect_sqlite_schema(database_path, db_id=db_id)
        policy: MSchemaSamplePolicy | None = None
        if prompt_variant == "question_only":
            prompt = build_question_only_prompt(question, schema.dialect)
            prompt_version = QUESTION_ONLY_PROMPT_VERSION
            schema_representation = "none"
        elif prompt_variant == "simple_schema":
            prompt = build_baseline_prompt(question, schema)
            prompt_version = SIMPLE_SCHEMA_PROMPT_VERSION
            schema_representation = "simple"
        elif prompt_variant == "mschema":
            policy = mschema_sample_policy or MSchemaSamplePolicy()
            resolved_path = Path(database_path).expanduser().resolve()
            schema_hash = canonical_schema_sha256(schema)
            cache_key = (resolved_path, schema_hash, policy)
            examples = self._mschema_cache.get(cache_key)
            if examples is None:
                examples = sample_sqlite_mschema_values(
                    resolved_path, schema, policy
                )
                self._mschema_cache[cache_key] = examples
            prompt = build_mschema_prompt(question, schema, examples)
            prompt_version = MSCHEMA_PROMPT_VERSION
            schema_representation = MSCHEMA_VERSION
        else:
            raise ValueError(f"Unknown prompt variant: {prompt_variant!r}")
        if prompt_variant != "mschema" and mschema_sample_policy is not None:
            raise ValueError("M-Schema sample policy requires the mschema prompt variant")

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

        return GenerationResult(
            run_id=str(uuid.uuid4()),
            db_id=schema.db_id,
            question=question,
            dialect=schema.dialect,
            provider=self.provider.provider_name,
            model_id=self.provider.model_id,
            prompt_version=prompt_version,
            prompt_hash=_sha256(prompt),
            schema_hash=canonical_schema_sha256(schema),
            generated_sql=response.candidates,
            selected_sql=selected_sql,
            validation_status="not_implemented",
            execution_status="not_executed",
            latency_ms=latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            metadata={
                "phase": 3 if prompt_variant == "mschema" else 2,
                "prompt_variant": prompt_variant,
                "schema_representation": schema_representation,
                "mschema_sample_policy": (
                    {
                        "examples_per_column": policy.examples_per_column,
                        "max_text_length": policy.max_text_length,
                        "scan_rows_per_column": policy.scan_rows_per_column,
                    }
                    if policy is not None
                    else None
                ),
                "provider": response.metadata,
            },
        )

