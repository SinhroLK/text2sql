from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.evaluation import (
    EvaluationResourceError,
    Spider2GoldResultRunner,
)
from text2sql.observability import append_jsonl
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import SQLProvider
from text2sql.schema import (
    MSchemaSamplePolicy,
    RecallSchemaLinkingPolicy,
    SchemaLinkingPolicy,
)

from .config import BaselineExperimentConfig


class ExperimentRunError(RuntimeError):
    def __init__(
        self, code: str, message: str, **context: Any
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context = context


def _percentile(values: list[int], probability: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def _schema_linking_summary(
    generations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    decisions = [
        generation.get("metadata", {}).get("schema_linking")
        for generation in generations
    ]
    if all(decision is None for decision in decisions):
        return None
    if any(not isinstance(decision, dict) for decision in decisions):
        raise ExperimentRunError(
            "invalid_schema_linking_audit",
            "Schema-linked generations must all contain audit metadata",
        )
    linked = [decision for decision in decisions if isinstance(decision, dict)]
    original_tables = sum(
        int(decision["original_table_count"]) for decision in linked
    )
    selected_tables = sum(
        int(decision["selected_table_count"]) for decision in linked
    )
    original_columns = sum(
        int(decision["original_column_count"]) for decision in linked
    )
    selected_columns = sum(
        int(decision["selected_column_count"]) for decision in linked
    )
    selected_table_counts = [
        int(decision["selected_table_count"]) for decision in linked
    ]
    selected_column_counts = [
        int(decision["selected_column_count"]) for decision in linked
    ]
    return {
        "version": linked[0]["version"],
        "total": len(linked),
        "fallback_count": sum(
            bool(decision["fallback_used"]) for decision in linked
        ),
        "original_table_count_total": original_tables,
        "selected_table_count_total": selected_tables,
        "table_reduction_ratio": (
            0.0
            if original_tables == 0
            else 1.0 - selected_tables / original_tables
        ),
        "selected_tables_p50": _percentile(
            selected_table_counts, 0.50
        ),
        "selected_tables_p95": _percentile(
            selected_table_counts, 0.95
        ),
        "original_column_count_total": original_columns,
        "selected_column_count_total": selected_columns,
        "column_reduction_ratio": (
            0.0
            if original_columns == 0
            else 1.0 - selected_columns / original_columns
        ),
        "selected_columns_p50": _percentile(
            selected_column_counts, 0.50
        ),
        "selected_columns_p95": _percentile(
            selected_column_counts, 0.95
        ),
    }


def _read_checkpoint(
    path: Path,
    *,
    config: BaselineExperimentConfig,
    expected_by_id: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ExperimentRunError(
                    "invalid_checkpoint",
                    f"Blank checkpoint line {line_number}",
                )
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ExperimentRunError(
                    "invalid_checkpoint",
                    f"Invalid JSON on checkpoint line {line_number}",
                ) from error
            example_id = (
                record.get("example_id")
                if isinstance(record, dict)
                else None
            )
            if (
                not isinstance(example_id, str)
                or example_id not in expected_by_id
            ):
                raise ExperimentRunError(
                    "checkpoint_coverage_mismatch",
                    "Checkpoint contains an ID outside the frozen "
                    "development scope",
                    example_id=example_id,
                )
            if example_id in records:
                raise ExperimentRunError(
                    "duplicate_checkpoint_id",
                    f"Duplicate checkpoint ID {example_id}",
                    example_id=example_id,
                )
            if (
                record.get("experiment_id") != config.experiment_id
                or record.get("config_sha256") != config.config_sha256
            ):
                raise ExperimentRunError(
                    "checkpoint_configuration_mismatch",
                    "Checkpoint was created by a different experiment "
                    "configuration",
                    example_id=example_id,
                )
            expected = expected_by_id[example_id]
            if record.get("db_id") != expected.db_id:
                raise ExperimentRunError(
                    "checkpoint_database_mismatch",
                    "Checkpoint db_id does not match frozen DATA-003 "
                    "metadata",
                    example_id=example_id,
                )
            generated_sql = record.get("generated_sql")
            if (
                not isinstance(generated_sql, str)
                or not generated_sql.strip()
            ):
                raise ExperimentRunError(
                    "invalid_checkpoint",
                    "Checkpoint generated_sql must be non-empty",
                    example_id=example_id,
                )
            records[example_id] = record
    return records


class BaselineExperimentRunner:
    def __init__(
        self,
        *,
        config: BaselineExperimentConfig,
        dataset: LoadedSpider2LiteDataset,
        pipeline: Text2SQLPipeline,
        evaluator: Spider2GoldResultRunner,
    ) -> None:
        if config.split != "development":
            raise ExperimentRunError(
                "split_firewall",
                "Baseline experiments may access only development",
            )
        if pipeline.provider.model_id != config.model_id:
            raise ExperimentRunError(
                "model_configuration_mismatch",
                "Provider model_id does not match the frozen "
                "experiment config",
            )
        self.config = config
        self.dataset = dataset
        self.pipeline = pipeline
        self.evaluator = evaluator

    def run(
        self, predictions_path: str | Path, report_path: str | Path
    ) -> dict[str, Any]:
        predictions = Path(predictions_path).expanduser().resolve()
        report = Path(report_path).expanduser().resolve()
        examples = tuple(
            sorted(
                self.dataset.for_split("development"),
                key=lambda item: item.example_id,
            )
        )
        if not examples:
            raise ExperimentRunError(
                "empty_split", "Development split is empty"
            )
        expected_by_id = {
            example.example_id: example for example in examples
        }
        if len(expected_by_id) != len(examples):
            raise ExperimentRunError(
                "duplicate_example_id",
                "Development example IDs are not unique",
            )

        checkpoint = _read_checkpoint(
            predictions,
            config=self.config,
            expected_by_id=expected_by_id,
        )
        for example in examples:
            if example.example_id in checkpoint:
                continue
            database = self.evaluator.database_resolver.resolve(
                example.db_id
            )
            uses_mschema = self.config.baseline in {"B2", "B6", "B6R"}
            generated = self.pipeline.generate(
                example.question,
                database.path,
                db_id=example.db_id,
                prompt_variant=self.config.prompt_variant,
                mschema_sample_policy=(
                    MSchemaSamplePolicy(
                        examples_per_column=(
                            self.config.mschema_examples_per_column
                        ),
                        max_text_length=(
                            self.config.mschema_max_text_length
                        ),
                        scan_rows_per_column=(
                            self.config.mschema_scan_rows_per_column
                        ),
                    )
                    if uses_mschema
                    else None
                ),
                schema_linking_policy=(
                    (
                        RecallSchemaLinkingPolicy
                        if self.config
                        .schema_link_include_all_selected_table_columns
                        else SchemaLinkingPolicy
                    )(
                        max_tables=self.config.schema_link_max_tables,
                        max_columns_per_table=(
                            self.config.schema_link_max_columns_per_table
                        ),
                        minimum_columns_per_table=(
                            self.config
                            .schema_link_minimum_columns_per_table
                        ),
                        min_score=self.config.schema_link_min_score,
                        include_value_matches=(
                            self.config
                            .schema_link_include_value_matches
                        ),
                        include_foreign_key_closure=(
                            self.config
                            .schema_link_include_foreign_key_closure
                        ),
                        fallback_mode=(
                            self.config.schema_link_fallback_mode
                            or "full_schema"
                        ),
                    )
                    if self.config.baseline in {"B6", "B6R"}
                    else None
                ),
            )
            if not generated.selected_sql:
                raise ExperimentRunError(
                    "empty_generation",
                    "Provider returned no selected SQL",
                    example_id=example.example_id,
                )
            record = {
                "schema_version": 1,
                "experiment_id": self.config.experiment_id,
                "config_sha256": self.config.config_sha256,
                "baseline": self.config.baseline,
                "example_id": example.example_id,
                "db_id": example.db_id,
                "generated_sql": generated.selected_sql,
                "generation": generated.to_dict(),
            }
            append_jsonl(predictions, record)
            checkpoint[example.example_id] = record

        missing = sorted(set(expected_by_id) - set(checkpoint))
        extra = sorted(set(checkpoint) - set(expected_by_id))
        if missing or extra:
            raise ExperimentRunError(
                "prediction_coverage_mismatch",
                "Prediction checkpoint does not exactly cover development",
                missing=missing,
                extra=extra,
            )
        generated_sql = {
            example_id: checkpoint[example_id]["generated_sql"]
            for example_id in sorted(checkpoint)
        }
        try:
            evaluation = self.evaluator.evaluate_batch(
                generated_sql, split="development"
            )
        except EvaluationResourceError as error:
            raise ExperimentRunError(
                "evaluation_failed",
                str(error),
                evaluation_code=error.code,
            ) from error

        generations = [
            checkpoint[item]["generation"] for item in sorted(checkpoint)
        ]
        latencies = [
            int(item["latency_ms"]) for item in generations
        ]
        input_tokens = sum(
            int(item["input_tokens"]) for item in generations
        )
        output_tokens = sum(
            int(item["output_tokens"]) for item in generations
        )
        uses_mschema = self.config.baseline in {"B2", "B6", "B6R"}
        payload = {
            "schema_version": 1,
            "experiment": {
                "experiment_id": self.config.experiment_id,
                "baseline": self.config.baseline,
                "split": self.config.split,
                "prompt_variant": self.config.prompt_variant,
                "provider": self.config.provider,
                "model_id": self.config.model_id,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "max_retries": self.config.max_retries,
                "seed": self.config.seed,
                "reasoning_effort": self.config.reasoning_effort,
                "mschema_sample_policy": (
                    {
                        "examples_per_column": (
                            self.config.mschema_examples_per_column
                        ),
                        "max_text_length": (
                            self.config.mschema_max_text_length
                        ),
                        "scan_rows_per_column": (
                            self.config.mschema_scan_rows_per_column
                        ),
                    }
                    if uses_mschema
                    else None
                ),
                "schema_linking_policy": (
                    {
                        "version": self.config.schema_linker_version,
                        "max_tables": (
                            self.config.schema_link_max_tables
                        ),
                        "max_columns_per_table": (
                            self.config
                            .schema_link_max_columns_per_table
                        ),
                        "minimum_columns_per_table": (
                            self.config
                            .schema_link_minimum_columns_per_table
                        ),
                        "min_score": self.config.schema_link_min_score,
                        "include_value_matches": (
                            self.config
                            .schema_link_include_value_matches
                        ),
                        "include_foreign_key_closure": (
                            self.config
                            .schema_link_include_foreign_key_closure
                        ),
                        **(
                            {
                                "include_all_selected_table_columns": (
                                    self.config
                                    .schema_link_include_all_selected_table_columns
                                )
                            }
                            if self.config.baseline == "B6R"
                            else {}
                        ),
                        "fallback_mode": (
                            self.config.schema_link_fallback_mode
                        ),
                    }
                    if self.config.baseline in {"B6", "B6R"}
                    else None
                ),
                "timeout_seconds": self.config.timeout_seconds,
                "config_sha256": self.config.config_sha256,
            },
            "generation_summary": {
                "total": len(generations),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms_total": sum(latencies),
                "latency_ms_p50": _percentile(latencies, 0.50),
                "latency_ms_p95": _percentile(latencies, 0.95),
                "valid_sql_rate": (
                    evaluation.evaluated / evaluation.total
                ),
                "schema_linking": _schema_linking_summary(
                    generations
                ),
            },
            "resources": {
                "dataset_manifest": self.dataset.manifest,
                "evaluation_manifest": (
                    self.evaluator.resource_manifest(
                        split="development"
                    )
                ),
            },
            "evaluation": evaluation.to_dict(),
        }
        rendered = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        temporary = report.with_name(f".{report.name}.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(report)
        return payload
