from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.domain import Text2SQLExample

from .evaluator import SQLiteExecutionEvaluator, summarize_execution_accuracy
from .models import ExecutionEvaluationResult
from .resources import (
    EvaluationResourceError,
    ProtectedReferenceSQLStore,
    Spider2SQLiteDatabaseResolver,
    sha256_path,
)


@dataclass(frozen=True)
class Spider2EvaluationRecord:
    result: ExecutionEvaluationResult
    database_sha256: str
    reference_sql_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict(),
            "resources": {
                "database_sha256": self.database_sha256,
                "reference_sql_sha256": self.reference_sql_sha256,
            },
        }


@dataclass(frozen=True)
class Spider2BatchEvaluation:
    split: str
    expected_ids: tuple[str, ...]
    total: int
    evaluated: int
    correct: int
    incorrect: int
    execution_errors: int
    comparison_errors: int
    execution_accuracy: float
    records: tuple[Spider2EvaluationRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["records"] = [record.to_dict() for record in self.records]
        return payload


@dataclass(frozen=True)
class Spider2EvaluationPreflight:
    split: str
    expected_examples: int
    expected_databases: int
    missing_database_ids: tuple[str, ...]
    missing_reference_ids: tuple[str, ...]
    ready: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_generated_sql_jsonl(path: str | Path) -> dict[str, str]:
    predictions: dict[str, str] = {}
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise EvaluationResourceError(
            "predictions_not_found",
            "Generated SQL JSONL not found",
            expected_path=str(source),
        )
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise EvaluationResourceError(
                    "invalid_prediction",
                    f"Invalid JSON on predictions line {line_number}",
                ) from error
            example_id = record.get("example_id") if isinstance(record, dict) else None
            generated_sql = record.get("generated_sql") if isinstance(record, dict) else None
            if not isinstance(example_id, str) or not example_id:
                raise EvaluationResourceError(
                    "invalid_prediction",
                    f"Missing example_id on predictions line {line_number}",
                )
            if example_id in predictions:
                raise EvaluationResourceError(
                    "duplicate_prediction_id",
                    f"Duplicate generated SQL for {example_id}",
                    example_id=example_id,
                )
            if not isinstance(generated_sql, str) or not generated_sql.strip():
                raise EvaluationResourceError(
                    "invalid_prediction",
                    f"generated_sql must be non-empty for {example_id}",
                    example_id=example_id,
                )
            predictions[example_id] = generated_sql.strip()
    return predictions


class Spider2EvaluationRunner:
    """Connect DATA-003 metadata to protected resources and EVAL-001."""

    def __init__(
        self,
        *,
        dataset: LoadedSpider2LiteDataset,
        database_resolver: Spider2SQLiteDatabaseResolver,
        references: ProtectedReferenceSQLStore,
        evaluator: SQLiteExecutionEvaluator | None = None,
    ) -> None:
        self.dataset = dataset
        self.database_resolver = database_resolver
        self.references = references
        self.evaluator = evaluator or SQLiteExecutionEvaluator()
        self._examples = self._index_examples(dataset.examples)

    @staticmethod
    def _index_examples(examples: Iterable[Text2SQLExample]) -> dict[str, Text2SQLExample]:
        indexed: dict[str, Text2SQLExample] = {}
        for example in examples:
            if example.example_id in indexed:
                raise EvaluationResourceError(
                    "duplicate_example_id",
                    f"Duplicate DATA-003 example ID {example.example_id}",
                    example_id=example.example_id,
                )
            indexed[example.example_id] = example
        return indexed

    def evaluate_one(self, example_id: str, generated_sql: str) -> Spider2EvaluationRecord:
        try:
            example = self._examples[example_id]
        except KeyError as error:
            raise EvaluationResourceError(
                "example_not_found",
                f"DATA-003 example not found: {example_id}",
                example_id=example_id,
            ) from error

        database = self.database_resolver.resolve(example.db_id)
        reference = self.references.get(example_id)
        variant_results = tuple(
            self.evaluator.evaluate(
                example=example,
                database_path=database.path,
                generated_sql=generated_sql,
                reference_sql=reference.sql,
                condition_cols=condition_cols,
                ignore_order=reference.ignore_order,
            )
            for condition_cols in reference.condition_cols_variants
        )
        result = next((item for item in variant_results if item.correct), variant_results[0])
        return Spider2EvaluationRecord(
            result=result,
            database_sha256=database.sha256,
            reference_sql_sha256=reference.sql_sha256,
        )

    def preflight(self, *, split: str = "development") -> Spider2EvaluationPreflight:
        examples = self.dataset.for_split(split)
        expected_ids = tuple(example.example_id for example in examples)
        expected_db_ids = tuple(example.db_id for example in examples)
        missing_databases = self.database_resolver.missing_db_ids(expected_db_ids)
        missing_references = self.references.missing_example_ids(expected_ids)
        return Spider2EvaluationPreflight(
            split=split,
            expected_examples=len(expected_ids),
            expected_databases=len(set(expected_db_ids)),
            missing_database_ids=missing_databases,
            missing_reference_ids=missing_references,
            ready=not (missing_databases or missing_references),
        )

    def evaluate_batch(
        self,
        generated_sql_by_id: Mapping[str, str],
        *,
        split: str = "development",
    ) -> Spider2BatchEvaluation:
        examples = self.dataset.for_split(split)
        expected_ids = tuple(sorted(example.example_id for example in examples))
        prediction_ids = tuple(generated_sql_by_id)
        if len(set(prediction_ids)) != len(prediction_ids):
            raise EvaluationResourceError("duplicate_prediction_id", "Duplicate prediction IDs")
        missing = sorted(set(expected_ids) - set(prediction_ids))
        extra = sorted(set(prediction_ids) - set(expected_ids))
        if missing or extra:
            raise EvaluationResourceError(
                "prediction_coverage_mismatch",
                "Generated SQL coverage does not exactly match the requested split",
                missing=missing,
                extra=extra,
            )

        self.references.validate_coverage(expected_ids)
        self.database_resolver.validate_coverage(example.db_id for example in examples)
        records = tuple(self.evaluate_one(example_id, generated_sql_by_id[example_id]) for example_id in expected_ids)
        results = tuple(record.result for record in records)
        accuracy = summarize_execution_accuracy(results, expected_ids=expected_ids)
        correct = sum(result.status == "correct" for result in results)
        incorrect = sum(result.status == "result_mismatch" for result in results)
        execution_errors = sum(result.status.endswith("execution_error") for result in results)
        comparison_errors = sum(result.status == "comparison_error" for result in results)
        return Spider2BatchEvaluation(
            split=split,
            expected_ids=expected_ids,
            total=accuracy.total,
            evaluated=correct + incorrect,
            correct=correct,
            incorrect=incorrect,
            execution_errors=execution_errors,
            comparison_errors=comparison_errors,
            execution_accuracy=accuracy.execution_accuracy,
            records=records,
        )

    def resource_manifest(self, *, split: str = "development") -> dict[str, Any]:
        preflight = self.preflight(split=split)
        if not preflight.ready:
            raise EvaluationResourceError(
                "evaluation_preflight_failed",
                "Cannot freeze resources until all split resources are present",
                preflight=preflight.to_dict(),
            )
        examples = self.dataset.for_split(split)
        databases = self.database_resolver.validate_coverage(example.db_id for example in examples)
        references = tuple(self.references.get(example.example_id) for example in examples)
        source = self.dataset.manifest.get("source", {})
        return {
            "schema_version": 1,
            "dataset_id": self.dataset.manifest.get("dataset_id"),
            "source_commit": source.get("commit"),
            "split": split,
            "expected_example_ids": [example.example_id for example in examples],
            "databases": [
                {
                    "db_id": database.db_id,
                    "relative_path": database.path.relative_to(
                        self.database_resolver.database_dir
                    ).as_posix(),
                    "sha256": database.sha256,
                    "size_bytes": database.path.stat().st_size,
                }
                for database in databases
            ],
            "reference_sql": [
                {
                    "example_id": reference.example_id,
                    "relative_path": reference.path.relative_to(
                        self.references.sql_dir
                    ).as_posix(),
                    "sha256": reference.sql_sha256,
                }
                for reference in references
            ],
            "evaluation_metadata": {
                "sha256": sha256_path(self.references.evaluation_metadata_path),
            },
        }
