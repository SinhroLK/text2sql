from __future__ import annotations

import hashlib
import json
import time
import tomllib
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.evaluation import (
    Spider2BatchEvaluation,
    Spider2GoldResultRunner,
    Spider2SQLiteDatabaseResolver,
)
from text2sql.experiments.config import (
    BaselineExperimentConfig,
    ExperimentConfigurationError,
    load_baseline_config,
)
from text2sql.experiments.runner import (
    ExperimentRunError,
    _percentile,
    _retrieval_summary,
    _schema_linking_summary,
)
from text2sql.observability import append_jsonl
from text2sql.pipeline import Text2SQLPipeline
from text2sql.prompting import FewShotExample
from text2sql.providers import GroqProviderError
from text2sql.retrieval import LoadedRetrievalIndex, build_retrieval_selector
from text2sql.schema import (
    MSchemaSamplePolicy,
    RecallSchemaLinkingPolicy,
    SchemaLinkingPolicy,
)

from .freeze import ARM_CONFIGS, FINAL_ARMS


PROVIDER_FAILURE_SQL = "SELECT FROM"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_final_config(
    project_root: str | Path, arm: str
) -> BaselineExperimentConfig:
    root = Path(project_root).resolve()
    if arm not in FINAL_ARMS:
        raise ExperimentConfigurationError(f"Unknown final arm: {arm}")
    template_relative, final_relative = ARM_CONFIGS[arm]
    template = load_baseline_config(root / template_relative)
    final_path = root / final_relative
    try:
        raw = final_path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ExperimentConfigurationError(
            f"Invalid final experiment config: {final_path}"
        ) from error
    expected_id = f"final001-{arm.casefold()}-test-gpt-oss-120b-v1"
    if (
        data.get("baseline") != arm
        or data.get("split") != "test"
        or data.get("experiment_id") != expected_id
    ):
        raise ExperimentConfigurationError(
            f"Final {arm} config has the wrong identity"
        )
    return replace(
        template,
        experiment_id=expected_id,
        split="test",
        config_path=final_path,
        config_sha256=hashlib.sha256(raw).hexdigest(),
    )




def _failure_record(
    *,
    config: BaselineExperimentConfig,
    example: Any,
    provider_name: str,
    model_id: str,
    error_category: str,
    latency_ms: int,
) -> dict[str, Any]:
    generation = {
        "run_id": str(uuid.uuid4()),
        "db_id": example.db_id,
        "question": example.question,
        "dialect": "sqlite",
        "provider": provider_name,
        "model_id": model_id,
        "prompt_version": None,
        "prompt_hash": None,
        "schema_hash": None,
        "generated_sql": [],
        "selected_sql": None,
        "validation_status": "provider_error",
        "execution_status": "not_executed",
        "latency_ms": latency_ms,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": None,
        "error_category": error_category,
        "metadata": {
            "payload_retained": False,
        },
    }
    return {
        "schema_version": 1,
        "experiment_id": config.experiment_id,
        "config_sha256": config.config_sha256,
        "baseline": config.baseline,
        "example_id": example.example_id,
        "db_id": example.db_id,
        "generated_sql": PROVIDER_FAILURE_SQL,
        "generation": generation,
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
                    "invalid_final_checkpoint",
                    f"Blank final checkpoint line {line_number}",
                )
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ExperimentRunError(
                    "invalid_final_checkpoint",
                    f"Invalid JSON on final checkpoint line {line_number}",
                ) from error
            example_id = record.get("example_id")
            if (
                not isinstance(example_id, str)
                or example_id not in expected_by_id
                or example_id in records
            ):
                raise ExperimentRunError(
                    "final_checkpoint_coverage_mismatch",
                    "Final checkpoint has an unknown or duplicate example ID",
                    example_id=example_id,
                )
            expected = expected_by_id[example_id]
            if (
                record.get("schema_version") != 1
                or record.get("experiment_id") != config.experiment_id
                or record.get("config_sha256") != config.config_sha256
                or record.get("baseline") != config.baseline
                or record.get("db_id") != expected.db_id
                or not isinstance(record.get("generated_sql"), str)
                or not record["generated_sql"].strip()
                or not isinstance(record.get("generation"), dict)
            ):
                raise ExperimentRunError(
                    "final_checkpoint_identity_mismatch",
                    "Final checkpoint does not match its frozen config or dataset",
                    example_id=example_id,
                )
            records[example_id] = record
    return records


class FinalExperimentGenerator:
    def __init__(
        self,
        *,
        config: BaselineExperimentConfig,
        dataset: LoadedSpider2LiteDataset,
        pipeline: Text2SQLPipeline,
        database_resolver: Spider2SQLiteDatabaseResolver,
        retrieval_index: LoadedRetrievalIndex | None = None,
        request_delay_seconds: float = 0.0,
        progress: Callable[[str, int, str], None] | None = None,
    ) -> None:
        if config.split != "test" or config.baseline not in FINAL_ARMS:
            raise ExperimentRunError(
                "final_split_firewall",
                "Final generation requires a frozen test-only configuration",
            )
        if pipeline.provider.model_id != config.model_id:
            raise ExperimentRunError(
                "model_configuration_mismatch",
                "Provider model_id does not match the frozen final config",
            )
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds must not be negative")
        self.config = config
        self.dataset = dataset
        self.pipeline = pipeline
        self.database_resolver = database_resolver
        self.request_delay_seconds = request_delay_seconds
        self.progress = progress
        self.retrieval_index = retrieval_index
        self.retrieval_selector = None
        if config.baseline == "B4":
            if retrieval_index is None:
                raise ExperimentRunError(
                    "missing_retrieval_index",
                    "Final B4 requires the verified retrieval index",
                )
            manifest = retrieval_index.manifest
            if (
                manifest.get("index_id") != config.retrieval_index_id
                or manifest.get("artifact", {}).get("sha256")
                != config.retrieval_index_sha256
                or retrieval_index.manifest_sha256
                != config.retrieval_manifest_sha256
            ):
                raise ExperimentRunError(
                    "retrieval_index_mismatch",
                    "Retrieval index does not match frozen final B4",
                )
            self.retrieval_selector = build_retrieval_selector(
                retrieval_index,
                strategy=config.retrieval_strategy or "",
                k=config.retrieval_k,
                seed=config.retrieval_seed,
            )
        elif retrieval_index is not None:
            raise ExperimentRunError(
                "unexpected_retrieval_index",
                "Only final B4 may receive a retrieval index",
            )

    def _generation_arguments(
        self, example: Any
    ) -> tuple[
        MSchemaSamplePolicy | None,
        SchemaLinkingPolicy | RecallSchemaLinkingPolicy | None,
        tuple[FewShotExample, ...],
        dict[str, Any] | None,
    ]:
        config = self.config
        uses_mschema = config.baseline in {"B2", "B4", "B6", "B6R"}
        sample_policy = (
            MSchemaSamplePolicy(
                examples_per_column=config.mschema_examples_per_column,
                max_text_length=config.mschema_max_text_length,
                scan_rows_per_column=config.mschema_scan_rows_per_column,
            )
            if uses_mschema
            else None
        )
        link_policy = (
            (
                RecallSchemaLinkingPolicy
                if config.schema_link_include_all_selected_table_columns
                else SchemaLinkingPolicy
            )(
                max_tables=config.schema_link_max_tables,
                max_columns_per_table=config.schema_link_max_columns_per_table,
                minimum_columns_per_table=(
                    config.schema_link_minimum_columns_per_table
                ),
                min_score=config.schema_link_min_score,
                include_value_matches=config.schema_link_include_value_matches,
                include_foreign_key_closure=(
                    config.schema_link_include_foreign_key_closure
                ),
                fallback_mode=config.schema_link_fallback_mode or "full_schema",
            )
            if config.baseline in {"B6", "B6R"}
            else None
        )
        selection = (
            self.retrieval_selector.select(example.question)
            if self.retrieval_selector is not None
            else None
        )
        if selection is None:
            return sample_policy, link_policy, (), None
        audit = {
            **selection.to_dict(),
            "target_example_id": example.example_id,
            "target_db_id": example.db_id,
            "index_id": config.retrieval_index_id,
            "index_sha256": config.retrieval_index_sha256,
            "manifest_sha256": config.retrieval_manifest_sha256,
            "seed": config.retrieval_seed,
        }
        few_shot = tuple(
            FewShotExample(
                retrieval_id=item.entry.retrieval_id,
                db_id=item.entry.db_id,
                question=item.entry.question,
                sql=item.entry.sql,
            )
            for item in selection.entries
        )
        return sample_policy, link_policy, few_shot, audit

    def generate(
        self, predictions_path: str | Path
    ) -> dict[str, dict[str, Any]]:
        predictions = Path(predictions_path).expanduser().resolve()
        examples = tuple(
            sorted(
                self.dataset.for_split("test"),
                key=lambda item: item.example_id,
            )
        )
        if len(examples) != 104:
            raise ExperimentRunError(
                "final_split_coverage_mismatch",
                "Final generation requires exactly 104 test examples",
                actual=len(examples),
            )
        expected_by_id = {
            example.example_id: example for example in examples
        }
        if len(expected_by_id) != len(examples):
            raise ExperimentRunError(
                "duplicate_example_id",
                "Final test example IDs are not unique",
            )
        checkpoint = _read_checkpoint(
            predictions,
            config=self.config,
            expected_by_id=expected_by_id,
        )
        pending_path = predictions.with_name(
            f".{predictions.name}.pending.json"
        )
        if pending_path.exists():
            try:
                pending = json.loads(
                    pending_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as error:
                raise ExperimentRunError(
                    "invalid_pending_attempt",
                    "Pending final attempt marker is invalid",
                ) from error
            pending_id = pending.get("example_id")
            if (
                pending.get("experiment_id") != self.config.experiment_id
                or pending.get("config_sha256")
                != self.config.config_sha256
                or pending_id not in expected_by_id
            ):
                raise ExperimentRunError(
                    "pending_attempt_identity_mismatch",
                    "Pending final attempt does not match this run",
                )
            if pending_id not in checkpoint:
                example = expected_by_id[pending_id]
                record = _failure_record(
                    config=self.config,
                    example=example,
                    provider_name=self.pipeline.provider.provider_name,
                    model_id=self.pipeline.provider.model_id,
                    error_category="ambiguous_interrupted_provider_attempt",
                    latency_ms=0,
                )
                append_jsonl(predictions, record)
                checkpoint[pending_id] = record
                if self.progress is not None:
                    self.progress(
                        pending_id,
                        len(checkpoint),
                        "ambiguous_interrupted_provider_attempt",
                    )
            pending_path.unlink()

        for position, example in enumerate(examples):
            if example.example_id in checkpoint:
                continue
            database = self.database_resolver.resolve(example.db_id)
            sample_policy, link_policy, few_shot, retrieval_audit = (
                self._generation_arguments(example)
            )
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            pending_path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "experiment_id": self.config.experiment_id,
                    "config_sha256": self.config.config_sha256,
                    "example_id": example.example_id,
                }, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            started = time.perf_counter()
            provider_failed = False
            try:
                generated = self.pipeline.generate(
                    example.question,
                    database.path,
                    db_id=example.db_id,
                    prompt_variant=self.config.prompt_variant,
                    mschema_sample_policy=sample_policy,
                    schema_linking_policy=link_policy,
                    few_shot_examples=few_shot,
                    retrieval_audit=retrieval_audit,
                )
                if not generated.selected_sql:
                    raise ExperimentRunError(
                        "empty_generation",
                        "Provider returned no selected SQL",
                        example_id=example.example_id,
                    )
                generated_sql = generated.selected_sql
                generation = generated.to_dict()
            except GroqProviderError:
                provider_failed = True
                record = _failure_record(
                    config=self.config,
                    example=example,
                    provider_name=self.pipeline.provider.provider_name,
                    model_id=self.pipeline.provider.model_id,
                    error_category="terminal_provider_error",
                    latency_ms=round(
                        (time.perf_counter() - started) * 1000
                    ),
                )
            else:
                record = {
                    "schema_version": 1,
                    "experiment_id": self.config.experiment_id,
                    "config_sha256": self.config.config_sha256,
                    "baseline": self.config.baseline,
                    "example_id": example.example_id,
                    "db_id": example.db_id,
                    "generated_sql": generated_sql,
                    "generation": generation,
                }
            append_jsonl(predictions, record)
            checkpoint[example.example_id] = record
            pending_path.unlink()
            if self.progress is not None:
                self.progress(
                    example.example_id,
                    len(checkpoint),
                    str(
                        record["generation"].get("error_category")
                        or "success"
                    ),
                )
            if provider_failed:
                raise ExperimentRunError(
                    "terminal_provider_failure_recorded",
                    "A terminal provider failure was retained. Resolve the "
                    "operational issue and rerun; this example will not be "
                    "replaced.",
                    example_id=example.example_id,
                )
            if (
                self.request_delay_seconds
                and position < len(examples) - 1
            ):
                time.sleep(self.request_delay_seconds)
        if set(checkpoint) != set(expected_by_id):
            raise ExperimentRunError(
                "final_prediction_coverage_mismatch",
                "Final checkpoint does not exactly cover the test split",
            )
        return checkpoint


def build_final_report(
    *,
    config: BaselineExperimentConfig,
    dataset: LoadedSpider2LiteDataset,
    evaluation_runner: Spider2GoldResultRunner,
    checkpoint: dict[str, dict[str, Any]],
    retrieval_index: LoadedRetrievalIndex | None,
    evaluation: Spider2BatchEvaluation,
) -> dict[str, Any]:
    generations = [
        checkpoint[item]["generation"] for item in sorted(checkpoint)
    ]
    successful = [
        item
        for item in generations
        if item.get("error_category") is None
    ]
    latencies = [int(item["latency_ms"]) for item in generations]
    uses_mschema = config.baseline in {"B2", "B4", "B6", "B6R"}
    return {
        "schema_version": 1,
        "experiment": {
            "experiment_id": config.experiment_id,
            "baseline": config.baseline,
            "split": "test",
            "prompt_variant": config.prompt_variant,
            "provider": config.provider,
            "model_id": config.model_id,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "max_retries": config.max_retries,
            "seed": config.seed,
            "reasoning_effort": config.reasoning_effort,
            "mschema_sample_policy": (
                {
                    "examples_per_column": config.mschema_examples_per_column,
                    "max_text_length": config.mschema_max_text_length,
                    "scan_rows_per_column": (
                        config.mschema_scan_rows_per_column
                    ),
                }
                if uses_mschema
                else None
            ),
            "schema_linking_policy": (
                {
                    "version": config.schema_linker_version,
                    "max_tables": config.schema_link_max_tables,
                    "max_columns_per_table": (
                        config.schema_link_max_columns_per_table
                    ),
                    "minimum_columns_per_table": (
                        config.schema_link_minimum_columns_per_table
                    ),
                    "min_score": config.schema_link_min_score,
                    "include_value_matches": (
                        config.schema_link_include_value_matches
                    ),
                    "include_foreign_key_closure": (
                        config.schema_link_include_foreign_key_closure
                    ),
                    **(
                        {
                            "include_all_selected_table_columns": (
                                config
                                .schema_link_include_all_selected_table_columns
                            )
                        }
                        if config.baseline == "B6R"
                        else {}
                    ),
                    "fallback_mode": config.schema_link_fallback_mode,
                }
                if config.baseline in {"B6", "B6R"}
                else None
            ),
            "retrieval_policy": (
                {
                    "index_id": config.retrieval_index_id,
                    "index_sha256": config.retrieval_index_sha256,
                    "manifest_sha256": config.retrieval_manifest_sha256,
                    "strategy": config.retrieval_strategy,
                    "k": config.retrieval_k,
                    "seed": config.retrieval_seed,
                }
                if config.baseline == "B4"
                else None
            ),
            "timeout_seconds": config.timeout_seconds,
            "config_sha256": config.config_sha256,
        },
        "generation_summary": {
            "total": len(generations),
            "provider_failures": len(generations) - len(successful),
            "input_tokens": sum(
                int(item["input_tokens"]) for item in generations
            ),
            "output_tokens": sum(
                int(item["output_tokens"]) for item in generations
            ),
            "latency_ms_total": sum(latencies),
            "latency_ms_p50": _percentile(latencies, 0.50),
            "latency_ms_p95": _percentile(latencies, 0.95),
            "valid_sql_rate": evaluation.evaluated / evaluation.total,
            "schema_linking": (
                _schema_linking_summary(successful) if successful else None
            ),
            "retrieval": (
                _retrieval_summary(successful) if successful else None
            ),
        },
        "resources": {
            "dataset_manifest": dataset.manifest,
            "evaluation_manifest": evaluation_runner.resource_manifest(
                split="test"
            ),
            "retrieval_manifest": (
                retrieval_index.manifest
                if retrieval_index is not None
                else None
            ),
        },
        "evaluation": evaluation.to_dict(),
    }


def write_json_immutable(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path).expanduser().resolve()
    rendered = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    if output.exists():
        if output.read_text(encoding="utf-8") != rendered:
            raise ExperimentRunError(
                "immutable_output_mismatch",
                f"Refusing to replace an existing different artifact: {output}",
            )
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(output)


def artifact_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }
