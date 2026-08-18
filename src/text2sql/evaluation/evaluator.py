from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from text2sql.domain import Text2SQLExample

from .comparator import SPIDER2_NUMERIC_TOLERANCE, compare_execution_results
from .models import ExecutionAccuracySummary, ExecutionEvaluationResult
from .sqlite_executor import SQLiteQueryExecutor


class SQLiteExecutionEvaluator:
    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self.executor = SQLiteQueryExecutor(timeout_seconds=timeout_seconds)

    def evaluate(
        self,
        *,
        example: Text2SQLExample,
        database_path: str | Path,
        generated_sql: str,
        reference_sql: str,
        condition_cols: Sequence[int] | None = None,
        ignore_order: bool = False,
        numeric_tolerance: float = SPIDER2_NUMERIC_TOLERANCE,
    ) -> ExecutionEvaluationResult:
        """Evaluate SQL for a DATA-003 example without reloading dataset metadata."""

        if example.dialect != "sqlite":
            raise ValueError(f"EVAL-001 supports only SQLite examples, got {example.dialect!r}")

        generated = self.executor.execute(database_path, generated_sql)
        reference = self.executor.execute(database_path, reference_sql)
        return self.evaluate_results(
            example=example,
            generated=generated,
            reference=reference,
            condition_cols=condition_cols,
            ignore_order=ignore_order,
            numeric_tolerance=numeric_tolerance,
        )

    def evaluate_results(
        self,
        *,
        example: Text2SQLExample,
        generated: QueryExecutionResult,
        reference: QueryExecutionResult,
        condition_cols: Sequence[int] | None = None,
        ignore_order: bool = False,
        numeric_tolerance: float = SPIDER2_NUMERIC_TOLERANCE,
    ) -> ExecutionEvaluationResult:
        """Compare materialized results, including official gold-result CSVs."""

        if example.dialect != "sqlite":
            raise ValueError(f"EVAL-001 supports only SQLite examples, got {example.dialect!r}")

        if not reference.succeeded:
            return ExecutionEvaluationResult(
                example_id=example.example_id,
                db_id=example.db_id,
                score=0,
                correct=False,
                status="reference_execution_error",
                generated=generated,
                reference=reference,
                error_category="reference_execution_error",
                error_message=reference.error_message,
            )
        if not generated.succeeded:
            return ExecutionEvaluationResult(
                example_id=example.example_id,
                db_id=example.db_id,
                score=0,
                correct=False,
                status="generated_execution_error",
                generated=generated,
                reference=reference,
                error_category="generated_execution_error",
                error_message=generated.error_message,
            )

        try:
            comparison = compare_execution_results(
                generated,
                reference,
                condition_cols=condition_cols,
                ignore_order=ignore_order,
                numeric_tolerance=numeric_tolerance,
            )
        except (TypeError, ValueError) as error:
            return ExecutionEvaluationResult(
                example_id=example.example_id,
                db_id=example.db_id,
                score=0,
                correct=False,
                status="comparison_error",
                generated=generated,
                reference=reference,
                error_category="comparison_error",
                error_message=str(error),
            )

        return ExecutionEvaluationResult(
            example_id=example.example_id,
            db_id=example.db_id,
            score=comparison.score,
            correct=comparison.equivalent,
            status="correct" if comparison.equivalent else "result_mismatch",
            generated=generated,
            reference=reference,
            comparison=comparison,
            error_category=None if comparison.equivalent else "result_mismatch",
            error_message=comparison.message,
        )


def summarize_execution_accuracy(
    results: Iterable[ExecutionEvaluationResult],
    *,
    expected_ids: Iterable[str] | None = None,
) -> ExecutionAccuracySummary:
    frozen_results = tuple(results)
    if not frozen_results:
        raise ValueError("Cannot calculate Execution Accuracy from an empty result set")

    result_ids = [result.example_id for result in frozen_results]
    if len(set(result_ids)) != len(result_ids):
        raise ValueError("Duplicate evaluation result IDs")

    expected = tuple(sorted(expected_ids if expected_ids is not None else result_ids))
    if len(set(expected)) != len(expected):
        raise ValueError("Duplicate expected evaluation IDs")
    missing = sorted(set(expected) - set(result_ids))
    extra = sorted(set(result_ids) - set(expected))
    if missing or extra:
        raise ValueError(f"Evaluation ID coverage mismatch; missing={missing}, extra={extra}")

    correct = sum(result.score for result in frozen_results)
    execution_errors = sum(
        result.status in {"generated_execution_error", "reference_execution_error"}
        for result in frozen_results
    )
    return ExecutionAccuracySummary(
        expected_ids=expected,
        total=len(frozen_results),
        correct=correct,
        incorrect=len(frozen_results) - correct,
        execution_errors=execution_errors,
        execution_accuracy=correct / len(frozen_results),
    )
