from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class QueryExecutionResult:
    status: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    duration_ms: int
    error_type: str | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True)
class ResultComparison:
    equivalent: bool
    score: int
    mode: str
    ignore_order: bool
    condition_cols: tuple[int, ...] | None
    numeric_tolerance: float
    matched_reference_columns: int
    reference_columns_considered: int
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionEvaluationResult:
    example_id: str
    db_id: str
    score: int
    correct: bool
    status: str
    generated: QueryExecutionResult
    reference: QueryExecutionResult
    comparison: ResultComparison | None = None
    error_category: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return _json_safe(payload)


@dataclass(frozen=True)
class ExecutionAccuracySummary:
    expected_ids: tuple[str, ...]
    total: int
    correct: int
    incorrect: int
    execution_errors: int
    execution_accuracy: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
