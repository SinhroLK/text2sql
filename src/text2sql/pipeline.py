from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

from text2sql.domain import GenerationInput, GenerationResult
from text2sql.prompting import (
    LINKED_MSCHEMA_PROMPT_VERSION,
    MSCHEMA_PROMPT_VERSION,
    QUESTION_ONLY_PROMPT_VERSION,
    RECALL_LINKED_MSCHEMA_PROMPT_VERSION,
    SIMPLE_SCHEMA_PROMPT_VERSION,
    build_baseline_prompt,
    build_linked_mschema_prompt,
    build_mschema_prompt,
    build_question_only_prompt,
    build_recall_linked_mschema_prompt,
)
from text2sql.providers import SQLProvider
from text2sql.schema import (
    MSCHEMA_VERSION,
    SCHEMA_LINKER_VERSION,
    MSchemaExamples,
    MSchemaSamplePolicy,
    RecallSchemaLinkingPolicy,
    SchemaLinkingPolicy,
    canonical_schema_sha256,
    inspect_sqlite_schema,
    link_schema,
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
        schema_linking_policy: SchemaLinkingPolicy | None = None,
    ) -> GenerationResult:
        schema = inspect_sqlite_schema(database_path, db_id=db_id)
        schema_hash = canonical_schema_sha256(schema)
        linking_variants = {
            "linked_mschema",
            "hybrid_linked_mschema",
        }
        mschema_variants = {"mschema", *linking_variants}
        if prompt_variant not in mschema_variants and mschema_sample_policy is not None:
            raise ValueError(
                "M-Schema sample policy requires an M-Schema prompt variant"
            )
        if (
            prompt_variant not in linking_variants
            and schema_linking_policy is not None
        ):
            raise ValueError(
                "Schema-linking policy requires linked_mschema or "
                "hybrid_linked_mschema"
            )
        if (
            prompt_variant == "hybrid_linked_mschema"
            and schema_linking_policy is not None
            and not isinstance(
                schema_linking_policy, RecallSchemaLinkingPolicy
            )
        ):
            raise ValueError(
                "hybrid_linked_mschema requires a recall schema-linking policy"
            )

        sample_policy: MSchemaSamplePolicy | None = None
        link_result = None
        generation_schema = schema
        if prompt_variant == "question_only":
            prompt = build_question_only_prompt(question, schema.dialect)
            prompt_version = QUESTION_ONLY_PROMPT_VERSION
            schema_representation = "none"
        elif prompt_variant == "simple_schema":
            prompt = build_baseline_prompt(question, schema)
            prompt_version = SIMPLE_SCHEMA_PROMPT_VERSION
            schema_representation = "simple"
        elif prompt_variant in mschema_variants:
            sample_policy = mschema_sample_policy or MSchemaSamplePolicy()
            resolved_path = Path(database_path).expanduser().resolve()
            cache_key = (resolved_path, schema_hash, sample_policy)
            examples = self._mschema_cache.get(cache_key)
            if examples is None:
                examples = sample_sqlite_mschema_values(
                    resolved_path, schema, sample_policy
                )
                self._mschema_cache[cache_key] = examples

            if prompt_variant in linking_variants:
                link_result = link_schema(
                    question,
                    schema,
                    examples,
                    schema_linking_policy
                    or (
                        RecallSchemaLinkingPolicy()
                        if prompt_variant == "hybrid_linked_mschema"
                        else SchemaLinkingPolicy()
                    ),
                )
                selected_columns = {
                    (table.name, column.name)
                    for table in link_result.schema.tables
                    for column in table.columns
                }
                linked_examples = {
                    key: values
                    for key, values in examples.items()
                    if key in selected_columns
                }
                if prompt_variant == "hybrid_linked_mschema":
                    generation_schema = schema
                    prompt = build_recall_linked_mschema_prompt(
                        question,
                        schema,
                        link_result.schema,
                        linked_examples,
                    )
                    prompt_version = RECALL_LINKED_MSCHEMA_PROMPT_VERSION
                    schema_representation = (
                        f"simple+{MSCHEMA_VERSION}+{SCHEMA_LINKER_VERSION}"
                    )
                else:
                    generation_schema = link_result.schema
                    prompt = build_linked_mschema_prompt(
                        question, generation_schema, linked_examples
                    )
                    prompt_version = LINKED_MSCHEMA_PROMPT_VERSION
                    schema_representation = (
                        f"{MSCHEMA_VERSION}+{SCHEMA_LINKER_VERSION}"
                    )
            else:
                prompt = build_mschema_prompt(question, schema, examples)
                prompt_version = MSCHEMA_PROMPT_VERSION
                schema_representation = MSCHEMA_VERSION
        else:
            raise ValueError(f"Unknown prompt variant: {prompt_variant!r}")

        generation_input = GenerationInput(
            question=question,
            prompt=prompt,
            schema=generation_schema,
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
            schema_hash=schema_hash,
            generated_sql=response.candidates,
            selected_sql=selected_sql,
            validation_status="not_implemented",
            execution_status="not_executed",
            latency_ms=latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            metadata={
                "phase": 3 if prompt_variant in mschema_variants else 2,
                "prompt_variant": prompt_variant,
                "schema_representation": schema_representation,
                "linked_schema_hash": (
                    canonical_schema_sha256(link_result.schema)
                    if link_result is not None
                    else None
                ),
                "schema_linking": (
                    link_result.to_dict() if link_result is not None else None
                ),
                "mschema_sample_policy": (
                    {
                        "examples_per_column": sample_policy.examples_per_column,
                        "max_text_length": sample_policy.max_text_length,
                        "scan_rows_per_column": sample_policy.scan_rows_per_column,
                    }
                    if sample_policy is not None
                    else None
                ),
                "provider": response.metadata,
            },
        )
