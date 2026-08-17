from __future__ import annotations

import math
from numbers import Real
from typing import Any, Iterable, Sequence

from .models import QueryExecutionResult, ResultComparison


SPIDER2_NUMERIC_TOLERANCE = 1e-2
SPIDER2_COMPARISON_MODE = "spider2-lite-compatible-column-vectors"


def _normalize(value: Any) -> Any:
    if value is None:
        return 0
    if isinstance(value, float) and math.isnan(value):
        return 0
    return value


def _sort_key(value: Any) -> tuple[bool, str, bool]:
    return (value is None, str(value), isinstance(value, Real))


def _vectors_match(
    left: Iterable[Any],
    right: Iterable[Any],
    *,
    tolerance: float,
    ignore_order: bool,
) -> bool:
    left_values = [_normalize(value) for value in left]
    right_values = [_normalize(value) for value in right]

    if ignore_order:
        left_values = sorted(left_values, key=_sort_key)
        right_values = sorted(right_values, key=_sort_key)

    if len(left_values) != len(right_values):
        return False

    for left_value, right_value in zip(left_values, right_values):
        if isinstance(left_value, Real) and isinstance(right_value, Real):
            if not math.isclose(float(left_value), float(right_value), abs_tol=tolerance):
                return False
        elif left_value != right_value:
            return False
    return True


def _normalize_condition_cols(
    condition_cols: Sequence[int] | None,
    column_count: int,
) -> tuple[int, ...]:
    if condition_cols is None or len(condition_cols) == 0:
        return tuple(range(column_count))

    normalized: list[int] = []
    for index in condition_cols:
        if not isinstance(index, int):
            raise ValueError("condition_cols must contain only integer column indexes")
        resolved = index if index >= 0 else column_count + index
        if resolved < 0 or resolved >= column_count:
            raise ValueError(f"condition_cols index {index} is outside reference result columns")
        normalized.append(resolved)
    return tuple(normalized)


def _column_vectors(result: QueryExecutionResult, indexes: Sequence[int]) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(row[index] for row in result.rows) for index in indexes)


def compare_execution_results(
    generated: QueryExecutionResult,
    reference: QueryExecutionResult,
    *,
    condition_cols: Sequence[int] | None = None,
    ignore_order: bool = False,
    numeric_tolerance: float = SPIDER2_NUMERIC_TOLERANCE,
) -> ResultComparison:
    """Mirror Spider2-Lite's official column-vector result comparison semantics."""

    if not generated.succeeded or not reference.succeeded:
        raise ValueError("Only successful query executions can be compared")
    if numeric_tolerance < 0:
        raise ValueError("numeric_tolerance cannot be negative")

    reference_indexes = _normalize_condition_cols(condition_cols, len(reference.columns))
    generated_indexes = tuple(range(len(generated.columns)))
    reference_vectors = _column_vectors(reference, reference_indexes)
    generated_vectors = _column_vectors(generated, generated_indexes)

    matched = 0
    for reference_vector in reference_vectors:
        if any(
            _vectors_match(
                reference_vector,
                generated_vector,
                tolerance=numeric_tolerance,
                ignore_order=ignore_order,
            )
            for generated_vector in generated_vectors
        ):
            matched += 1

    equivalent = matched == len(reference_vectors)
    message = None if equivalent else "Generated execution result does not match reference result"
    frozen_condition_cols = None if condition_cols is None else tuple(condition_cols)
    return ResultComparison(
        equivalent=equivalent,
        score=int(equivalent),
        mode=SPIDER2_COMPARISON_MODE,
        ignore_order=ignore_order,
        condition_cols=frozen_condition_cols,
        numeric_tolerance=numeric_tolerance,
        matched_reference_columns=matched,
        reference_columns_considered=len(reference_vectors),
        message=message,
    )
