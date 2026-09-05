from __future__ import annotations

import fcntl
import hashlib
import json
import math
import subprocess
import sys
import sqlite3
from importlib import metadata
from pathlib import Path
from typing import Any

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.evaluation import (
    EvaluationResourceError,
    Spider2GoldResultRunner,
)
from text2sql.observability import append_jsonl
from text2sql.pipeline import Text2SQLPipeline
from text2sql.prompting import FewShotExample
from text2sql.providers import SQLProvider
from text2sql.retrieval import (
    LoadedRetrievalIndex,
    build_retrieval_selector,
)
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


def _retrieval_summary(
    generations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    audits = [
        generation.get("metadata", {}).get("retrieval")
        for generation in generations
    ]
    if all(audit is None for audit in audits):
        return None
    if any(not isinstance(audit, dict) for audit in audits):
        raise ExperimentRunError(
            "invalid_retrieval_audit",
            "Few-shot generations must all contain retrieval audit metadata",
        )
    resolved = [audit for audit in audits if isinstance(audit, dict)]
    strategies = {str(audit.get("strategy")) for audit in resolved}
    index_ids = {str(audit.get("index_id")) for audit in resolved}
    index_hashes = {str(audit.get("index_sha256")) for audit in resolved}
    k_values = {int(audit.get("k", 0)) for audit in resolved}
    if (
        len(strategies) != 1
        or len(index_ids) != 1
        or len(index_hashes) != 1
        or len(k_values) != 1
        or 0 in k_values
    ):
        raise ExperimentRunError(
            "invalid_retrieval_audit",
            "Retrieval audit policy is inconsistent across generations",
        )
    selected_ids: list[str] = []
    for audit in resolved:
        selected = audit.get("selected")
        if not isinstance(selected, list) or len(selected) not in k_values:
            raise ExperimentRunError(
                "invalid_retrieval_audit",
                "Retrieval audit selected entries are incomplete",
            )
        selected_ids.extend(str(item["retrieval_id"]) for item in selected)
    return {
        "strategy": next(iter(strategies)),
        "index_id": next(iter(index_ids)),
        "index_sha256": next(iter(index_hashes)),
        "k": next(iter(k_values)),
        "targets": len(resolved),
        "selections": len(selected_ids),
        "unique_retrieval_ids": len(set(selected_ids)),
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
            generation = record.get("generation")
            if not isinstance(generation, dict) or not isinstance(generation.get("metadata"), dict):
                raise ExperimentRunError("invalid_checkpoint", "Missing generation metadata", example_id=example_id)
            if config.baseline in {"B3", "B4"}:
                retrieval = (
                    record.get("generation", {})
                    .get("metadata", {})
                    .get("retrieval")
                )
                if (
                    not isinstance(retrieval, dict)
                    or retrieval.get("strategy")
                    != config.retrieval_strategy
                    or retrieval.get("index_id")
                    != config.retrieval_index_id
                    or retrieval.get("index_sha256")
                    != config.retrieval_index_sha256
                    or retrieval.get("k") != config.retrieval_k
                ):
                    raise ExperimentRunError(
                        "checkpoint_retrieval_mismatch",
                        "Checkpoint retrieval audit does not match config",
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
                or (not generated_sql.strip() and not (record.get("schema_version") == 2 and record.get("generation_status") == "failed" and record.get("failure_code") in {"incomplete_completion", "unsupported_completion", "empty_completion"}))
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
        retrieval_index: LoadedRetrievalIndex | None = None,
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
        self.retrieval_index = retrieval_index
        self.retrieval_selector = None
        if config.baseline in {"B3", "B4"}:
            if retrieval_index is None:
                raise ExperimentRunError(
                    "missing_retrieval_index",
                    "B3/B4 require a verified retrieval index",
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
                    "Retrieval index identity does not match frozen config",
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
                "Only B3/B4 may receive a retrieval index",
            )

    def run(self, predictions_path: str | Path, report_path: str | Path) -> dict[str, Any]:
        predictions = Path(predictions_path).expanduser().resolve()
        predictions.parent.mkdir(parents=True, exist_ok=True)
        # Keep the lock file: unlinking it could let another writer lock a
        # different inode while this writer still owns the original one.
        with Path(str(predictions) + ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ExperimentRunError("checkpoint_locked", "Another writer owns this checkpoint") from error
            return self._run(predictions, report_path)

    def _run(
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
        # Prepare every development input before making any paid call. Clear
        # samples so a repeated run cannot conceal database-value drift.
        self.pipeline.clear_sample_cache()
        prepared_by_id = {}
        database_hashes = {}
        for example in examples:
            database = self.evaluator.database_resolver.resolve(
                example.db_id
            )
            if example.db_id not in database_hashes:
                with database.path.open("rb") as stream:
                    database_hashes[example.db_id] = hashlib.file_digest(stream, "sha256").hexdigest()
                wal = Path(str(database.path) + "-wal")
                if wal.exists() and wal.stat().st_size:
                    raise ExperimentRunError("mutable_database", "Checkpoint inputs require a database without a live WAL")
            uses_mschema = self.config.baseline in {
                "B2", "B3", "B4", "B6", "B6R"
            }
            selection = (
                self.retrieval_selector.select(example.question)
                if self.retrieval_selector is not None
                else None
            )
            retrieval_audit = None
            few_shot_examples: tuple[FewShotExample, ...] = ()
            if selection is not None:
                retrieval_audit = {
                    **selection.to_dict(),
                    "target_example_id": example.example_id,
                    "target_db_id": example.db_id,
                    "index_id": self.config.retrieval_index_id,
                    "index_sha256": self.config.retrieval_index_sha256,
                    "manifest_sha256": (
                        self.config.retrieval_manifest_sha256
                    ),
                    "seed": self.config.retrieval_seed,
                }
                few_shot_examples = tuple(
                    FewShotExample(
                        retrieval_id=item.entry.retrieval_id,
                        db_id=item.entry.db_id,
                        question=item.entry.question,
                        sql=item.entry.sql,
                    )
                    for item in selection.entries
                )
            prepared_by_id[example.example_id] = self.pipeline.prepare(
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
                few_shot_examples=few_shot_examples,
                retrieval_audit=retrieval_audit,
            )
        runtime = {"provider": self.pipeline.provider.provider_name,
                   "model_id": self.pipeline.provider.model_id,
                   "implementation": type(self.pipeline.provider).__module__ + "." + type(self.pipeline.provider).__qualname__}
        if runtime["provider"] != self.config.provider or runtime["model_id"] != self.config.model_id:
            raise ExperimentRunError("runtime_configuration_mismatch", "Runtime provider/model differs from config")
        for key in ("temperature", "max_tokens", "seed", "reasoning_effort", "max_retries", "timeout_seconds"):
            value = getattr(self.pipeline.provider, key, None)
            runtime[key] = value
            if hasattr(self.pipeline.provider, key) and value != getattr(self.config, key):
                raise ExperimentRunError("runtime_configuration_mismatch", f"Runtime {key} differs from config")
        runtime["endpoint"] = getattr(self.pipeline.provider, "endpoint", None)
        project_root = Path(__file__).resolve().parents[3]
        implementation_files = sorted((project_root / "src/text2sql").rglob("*.py"))
        implementation_files += sorted(project_root.glob("requirements*.txt"))
        implementation_files += [project_root / "requirements.lock", project_root / "pyproject.toml"]
        dependencies = {}
        for package in ("groq", "httpx", "pydantic", "dspy", "litellm", "optuna"):
            try:
                dependencies[package] = metadata.version(package)
            except metadata.PackageNotFoundError:
                dependencies[package] = None
        implementation_hashes = {
            str(path.relative_to(project_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in implementation_files
        }
        contract = {
            "implementation_sha256": implementation_hashes,
            "runtime_versions": {"python": sys.version, "sqlite": sqlite3.sqlite_version, **dependencies},
            "version": "baseline-checkpoint-v2",
            "config_sha256": self.config.config_sha256,
            "runtime": runtime,
            "database_sha256": database_hashes,
            "dataset_manifest": self.dataset.manifest,
            "evaluation_manifest": self.evaluator.resource_manifest(split="development"),
            "inputs": {
                key: {"question": value.generation_input.question,
                      "db_id": value.generation_input.schema.db_id,
                      "schema_hash": value.schema_hash,
                      "prompt_version": value.prompt_version,
                      "prompt_hash": hashlib.sha256(value.generation_input.prompt.encode()).hexdigest(),
                      "metadata": value.metadata}
                for key, value in sorted(prepared_by_id.items())
            },
        }
        contract_json = json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False)
        contract_hash = hashlib.sha256(contract_json.encode()).hexdigest()
        contract = json.loads(contract_json)
        for key, record in checkpoint.items():
            if record.get("run_contract_sha256") != contract_hash:
                raise ExperimentRunError("checkpoint_provenance_mismatch", "Checkpoint has missing or incompatible runtime/input provenance; use a new output path", example_id=key)
            generation = record.get("generation")
            expected = contract["inputs"][key]
            if (
                not isinstance(generation, dict)
                or any(generation.get(field) != expected[field] for field in (
                    "question", "db_id", "schema_hash", "prompt_version", "prompt_hash"
                ))
                or generation.get("model_id") != runtime["model_id"]
                or generation.get("provider") != runtime["provider"]
            ):
                raise ExperimentRunError("checkpoint_provenance_mismatch", "Checkpoint generation identity differs from prepared input", example_id=key)
            failure = record.get("failure_code")
            provider_metadata = generation["metadata"].get("provider")
            failed = record.get("generation_status") == "failed"
            if (
                record.get("schema_version") != 2
                or record.get("generation_status") not in {"completed", "failed"}
                or not isinstance(provider_metadata, dict)
                or (failed and (
                    failure not in {"incomplete_completion", "unsupported_completion", "empty_completion"}
                    or provider_metadata.get("completion_failure") != failure
                    or record["generated_sql"] != ""
                    or generation.get("selected_sql") is not None
                    or generation.get("generated_sql") != []
                ))
                or (not failed and (failure is not None or provider_metadata.get("completion_failure") is not None))
            ):
                raise ExperimentRunError("invalid_checkpoint", "Inconsistent checkpoint completion state", example_id=key)
            if record["generated_sql"] != (generation.get("selected_sql") or ""):
                raise ExperimentRunError("invalid_checkpoint", "Checkpoint SQL differs from generation record", example_id=key)
        manifest_path = Path(str(predictions) + ".run.json")
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as error:
                raise ExperimentRunError("invalid_run_manifest", "Run manifest cannot be read") from error
            if not isinstance(manifest, dict) or manifest.get("contract") != contract or manifest.get("contract_sha256") != contract_hash:
                raise ExperimentRunError("checkpoint_provenance_mismatch", "Run manifest differs from current runtime/input contract")
        else:
            if checkpoint:
                raise ExperimentRunError("missing_run_manifest", "Existing checkpoint requires its original run manifest")
            def git_value(*args):
                try:
                    result = subprocess.run(["git", *args], cwd=project_root, capture_output=True, text=True, check=True, timeout=5)
                    return result.stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    return None
            revision = git_value("rev-parse", "HEAD")
            status = git_value("status", "--porcelain", "--untracked-files=normal")
            manifest = {"contract": contract, "contract_sha256": contract_hash,
                        "project_revision": revision,
                        "project_dirty": bool(status) if status is not None else None}
            # Written before the first provider call. A damaged manifest fails
            # closed; it is never inferred from whatever inputs exist later.
            with manifest_path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        for example in examples:
            if example.example_id in checkpoint:
                continue
            generated = self.pipeline.generate_prepared(prepared_by_id[example.example_id])
            failure = generated.metadata.get("provider", {}).get("completion_failure")
            if not generated.selected_sql and not failure:
                raise ExperimentRunError(
                    "empty_generation",
                    "Provider returned no selected SQL",
                    example_id=example.example_id,
                )
            record = {
                "schema_version": 2,
                "run_contract_sha256": contract_hash,
                "generation_status": "failed" if failure else "completed",
                "failure_code": failure,
                "experiment_id": self.config.experiment_id,
                "config_sha256": self.config.config_sha256,
                "baseline": self.config.baseline,
                "example_id": example.example_id,
                "db_id": example.db_id,
                "generated_sql": generated.selected_sql or "",
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
        uses_mschema = self.config.baseline in {
            "B2", "B3", "B4", "B6", "B6R"
        }
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
                "retrieval_policy": (
                    {
                        "index_id": self.config.retrieval_index_id,
                        "index_sha256": self.config.retrieval_index_sha256,
                        "manifest_sha256": (
                            self.config.retrieval_manifest_sha256
                        ),
                        "strategy": self.config.retrieval_strategy,
                        "k": self.config.retrieval_k,
                        "seed": self.config.retrieval_seed,
                    }
                    if self.config.baseline in {"B3", "B4"}
                    else None
                ),
                "timeout_seconds": self.config.timeout_seconds,
                "config_sha256": self.config.config_sha256,
            },
            "run_contract": contract,
            "run_contract_sha256": contract_hash,
            "generation_summary": {
                "failed_completions": sum(row.get("generation_status") == "failed" for row in checkpoint.values()),
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
                "retrieval": _retrieval_summary(generations),
            },
            "resources": {
                "dataset_manifest": self.dataset.manifest,
                "evaluation_manifest": (
                    self.evaluator.resource_manifest(
                        split="development"
                    )
                ),
                "retrieval_manifest": (
                    self.retrieval_index.manifest
                    if self.retrieval_index is not None
                    else None
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
