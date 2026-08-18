from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.domain import Text2SQLExample

from .evaluator import SQLiteExecutionEvaluator, summarize_execution_accuracy
from .models import ExecutionEvaluationResult, QueryExecutionResult
from .resources import (
    ConditionColumns,
    EvaluationResourceError,
    Spider2SQLiteDatabaseResolver,
    _condition_variants,
    sha256_path,
)
from .spider2_runner import Spider2BatchEvaluation


@dataclass(frozen=True)
class GoldResultVariant:
    path: Path
    sha256: str
    result: QueryExecutionResult
    condition_cols: ConditionColumns


@dataclass(frozen=True)
class OfficialGoldResult:
    example_id: str
    variants: tuple[GoldResultVariant, ...]
    ignore_order: bool


def _csv_value(value: str) -> Any:
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _load_csv(path: Path) -> QueryExecutionResult:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration as error:
            raise EvaluationResourceError(
                "invalid_gold_result", "Gold-result CSV is empty", path=str(path)
            ) from error
        rows = tuple(tuple(_csv_value(value) for value in row) for row in reader)
    if not header:
        raise EvaluationResourceError(
            "invalid_gold_result", "Gold-result CSV has no columns", path=str(path)
        )
    if any(len(row) != len(header) for row in rows):
        raise EvaluationResourceError(
            "invalid_gold_result", "Gold-result CSV has ragged rows", path=str(path)
        )
    return QueryExecutionResult(
        status="success",
        columns=tuple(header),
        rows=rows,
        row_count=len(rows),
        duration_ms=0,
    )


class OfficialGoldResultStore:
    """Versioned Spider 2 gold execution results; no reference SQL required."""

    def __init__(
        self,
        results: dict[str, OfficialGoldResult],
        result_dir: Path,
        metadata_path: Path,
    ) -> None:
        self._results = dict(results)
        self.result_dir = result_dir.resolve()
        self.evaluation_metadata_path = metadata_path.resolve()

    @classmethod
    def from_official_directory(
        cls,
        result_dir: str | Path,
        evaluation_metadata_jsonl: str | Path,
        *,
        expected_metadata_sha256: str | None = None,
    ) -> "OfficialGoldResultStore":
        directory = Path(result_dir).expanduser().resolve()
        metadata_path = Path(evaluation_metadata_jsonl).expanduser().resolve()
        if not directory.is_dir():
            raise EvaluationResourceError(
                "gold_result_directory_not_found",
                "Official gold-result directory not found",
                expected_path=str(directory),
            )
        if not metadata_path.is_file():
            raise EvaluationResourceError(
                "evaluation_metadata_not_found",
                "Spider2 evaluation metadata JSONL not found",
                expected_path=str(metadata_path),
            )
        metadata_sha256 = sha256_path(metadata_path)
        if expected_metadata_sha256 is not None and metadata_sha256 != expected_metadata_sha256:
            raise EvaluationResourceError(
                "evaluation_metadata_checksum_mismatch",
                "Spider2 evaluation metadata checksum does not match",
                expected_sha256=expected_metadata_sha256,
                actual_sha256=metadata_sha256,
            )

        metadata: dict[str, dict[str, Any]] = {}
        with metadata_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise EvaluationResourceError(
                        "invalid_evaluation_metadata",
                        f"Invalid JSON on evaluation metadata line {line_number}",
                    ) from error
                example_id = record.get("instance_id") if isinstance(record, dict) else None
                if not isinstance(example_id, str) or not example_id:
                    raise EvaluationResourceError(
                        "invalid_evaluation_metadata",
                        f"Missing instance_id on evaluation metadata line {line_number}",
                    )
                if example_id in metadata:
                    raise EvaluationResourceError(
                        "duplicate_reference_id",
                        f"Duplicate evaluation metadata for {example_id}",
                        example_id=example_id,
                    )
                metadata[example_id] = record

        grouped: dict[str, list[Path]] = {}
        for path in sorted(directory.glob("*.csv")):
            stem = path.stem
            if stem in metadata:
                example_id = stem
            else:
                base, separator, suffix = stem.rpartition("_")
                if not separator or len(suffix) != 1 or base not in metadata:
                    continue
                example_id = base
            if not example_id.startswith("local"):
                continue
            grouped.setdefault(example_id, []).append(path)

        results: dict[str, OfficialGoldResult] = {}
        for example_id, paths in grouped.items():
            record = metadata[example_id]
            conditions = _condition_variants(record.get("condition_cols"), example_id)
            if len(conditions) == 1 and len(paths) > 1:
                conditions = conditions * len(paths)
            if len(conditions) != len(paths):
                raise EvaluationResourceError(
                    "gold_result_variant_mismatch",
                    "Gold-result CSV and condition_cols variant counts differ",
                    example_id=example_id,
                    csv_variants=len(paths),
                    condition_variants=len(conditions),
                )
            ignore_order = record.get("ignore_order", False)
            if not isinstance(ignore_order, bool):
                raise EvaluationResourceError(
                    "invalid_evaluation_metadata",
                    "ignore_order must be a JSON boolean",
                    example_id=example_id,
                )
            variants = tuple(
                GoldResultVariant(path, sha256_path(path), _load_csv(path), condition)
                for path, condition in zip(paths, conditions, strict=True)
            )
            results[example_id] = OfficialGoldResult(example_id, variants, ignore_order)
        return cls(results, directory, metadata_path)

    def get(self, example_id: str) -> OfficialGoldResult:
        try:
            return self._results[example_id]
        except KeyError as error:
            raise EvaluationResourceError(
                "gold_result_not_found",
                f"Official gold result not found: {example_id}",
                example_id=example_id,
            ) from error

    def missing_example_ids(self, example_ids: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted(set(example_ids) - self._results.keys()))

    def validate_coverage(self, example_ids: Iterable[str]) -> tuple[OfficialGoldResult, ...]:
        ids = tuple(sorted(set(example_ids)))
        missing = self.missing_example_ids(ids)
        if missing:
            raise EvaluationResourceError(
                "gold_result_coverage_mismatch",
                "Official gold-result coverage is incomplete",
                missing=list(missing),
            )
        return tuple(self._results[example_id] for example_id in ids)


@dataclass(frozen=True)
class GoldResultEvaluationRecord:
    result: ExecutionEvaluationResult
    database_sha256: str
    matched_gold_result_sha256: str
    matched_gold_result_file: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict(),
            "resources": {
                "database_sha256": self.database_sha256,
                "matched_gold_result_sha256": self.matched_gold_result_sha256,
                "matched_gold_result_file": self.matched_gold_result_file,
            },
        }


@dataclass(frozen=True)
class GoldResultEvaluationPreflight:
    split: str
    expected_examples: int
    expected_databases: int
    missing_database_ids: tuple[str, ...]
    missing_gold_result_ids: tuple[str, ...]
    ready: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Spider2GoldResultRunner:
    """Evaluate generated SQLite SQL against official materialized results."""

    def __init__(
        self,
        *,
        dataset: LoadedSpider2LiteDataset,
        database_resolver: Spider2SQLiteDatabaseResolver,
        gold_results: OfficialGoldResultStore,
        evaluator: SQLiteExecutionEvaluator | None = None,
    ) -> None:
        self.dataset = dataset
        self.database_resolver = database_resolver
        self.gold_results = gold_results
        self.evaluator = evaluator or SQLiteExecutionEvaluator()
        self._examples: dict[str, Text2SQLExample] = {}
        for example in dataset.examples:
            if example.example_id in self._examples:
                raise EvaluationResourceError(
                    "duplicate_example_id",
                    f"Duplicate DATA-003 example ID {example.example_id}",
                )
            self._examples[example.example_id] = example

    def evaluate_one(self, example_id: str, generated_sql: str) -> GoldResultEvaluationRecord:
        try:
            example = self._examples[example_id]
        except KeyError as error:
            raise EvaluationResourceError(
                "example_not_found", f"DATA-003 example not found: {example_id}"
            ) from error
        database = self.database_resolver.resolve(example.db_id)
        gold = self.gold_results.get(example_id)
        generated = self.evaluator.executor.execute(database.path, generated_sql)
        evaluated = tuple(
            (
                self.evaluator.evaluate_results(
                    example=example,
                    generated=generated,
                    reference=variant.result,
                    condition_cols=variant.condition_cols,
                    ignore_order=gold.ignore_order,
                ),
                variant,
            )
            for variant in gold.variants
        )
        result, variant = next(
            ((item, candidate) for item, candidate in evaluated if item.correct),
            evaluated[0],
        )
        return GoldResultEvaluationRecord(
            result=result,
            database_sha256=database.sha256,
            matched_gold_result_sha256=variant.sha256,
            matched_gold_result_file=variant.path.name,
        )

    def preflight(self, *, split: str = "development") -> GoldResultEvaluationPreflight:
        examples = self.dataset.for_split(split)
        database_ids = tuple(example.db_id for example in examples)
        example_ids = tuple(example.example_id for example in examples)
        missing_databases = self.database_resolver.missing_db_ids(database_ids)
        missing_results = self.gold_results.missing_example_ids(example_ids)
        return GoldResultEvaluationPreflight(
            split=split,
            expected_examples=len(examples),
            expected_databases=len(set(database_ids)),
            missing_database_ids=missing_databases,
            missing_gold_result_ids=missing_results,
            ready=not (missing_databases or missing_results),
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
        databases = self.database_resolver.validate_coverage(
            example.db_id for example in examples
        )
        gold = self.gold_results.validate_coverage(
            example.example_id for example in examples
        )
        return {
            "schema_version": 1,
            "reference_mode": "official_gold_results",
            "split": split,
            "expected_example_ids": [example.example_id for example in examples],
            "evaluation_metadata_sha256": sha256_path(
                self.gold_results.evaluation_metadata_path
            ),
            "databases": [
                {"db_id": item.db_id, "sha256": item.sha256} for item in databases
            ],
            "gold_results": [
                {
                    "example_id": item.example_id,
                    "variants": [
                        {"file": variant.path.name, "sha256": variant.sha256}
                        for variant in item.variants
                    ],
                }
                for item in gold
            ],
        }

    def evaluate_batch(
        self, generated_sql_by_id: Mapping[str, str], *, split: str = "development"
    ) -> Spider2BatchEvaluation:
        examples = self.dataset.for_split(split)
        expected_ids = tuple(sorted(example.example_id for example in examples))
        missing = sorted(set(expected_ids) - set(generated_sql_by_id))
        extra = sorted(set(generated_sql_by_id) - set(expected_ids))
        if missing or extra:
            raise EvaluationResourceError(
                "prediction_coverage_mismatch",
                "Generated SQL coverage does not exactly match the requested split",
                missing=missing,
                extra=extra,
            )
        self.gold_results.validate_coverage(expected_ids)
        self.database_resolver.validate_coverage(example.db_id for example in examples)
        records = tuple(
            self.evaluate_one(example_id, generated_sql_by_id[example_id])
            for example_id in expected_ids
        )
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
