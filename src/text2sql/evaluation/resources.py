from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class EvaluationResourceError(RuntimeError):
    """Structured failure while resolving evaluation-only resources."""

    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "context": self.context}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ResolvedSQLiteDatabase:
    db_id: str
    path: Path
    sha256: str


class Spider2SQLiteDatabaseResolver:
    """Resolve a Spider2 db_id to exactly one local SQLite file."""

    def __init__(self, database_dir: str | Path) -> None:
        self.database_dir = Path(database_dir).expanduser().resolve()

    def resolve(self, db_id: str) -> ResolvedSQLiteDatabase:
        if not isinstance(db_id, str) or not db_id.strip():
            raise EvaluationResourceError("invalid_db_id", "db_id must be a non-empty string")
        if Path(db_id).name != db_id or db_id in {".", ".."}:
            raise EvaluationResourceError(
                "invalid_db_id",
                "db_id must not contain path components",
                db_id=db_id,
            )

        path = self.database_dir / f"{db_id}.sqlite"
        if not path.is_file():
            raise EvaluationResourceError(
                "database_not_found",
                f"SQLite database not found for db_id {db_id!r}",
                db_id=db_id,
                expected_path=str(path),
            )
        return ResolvedSQLiteDatabase(db_id=db_id, path=path, sha256=sha256_path(path))

    def validate_coverage(self, db_ids: Iterable[str]) -> tuple[ResolvedSQLiteDatabase, ...]:
        unique_ids = tuple(sorted(set(db_ids)))
        return tuple(self.resolve(db_id) for db_id in unique_ids)

    def missing_db_ids(self, db_ids: Iterable[str]) -> tuple[str, ...]:
        return tuple(
            db_id
            for db_id in sorted(set(db_ids))
            if not (self.database_dir / f"{db_id}.sqlite").is_file()
        )


ConditionColumns = tuple[int, ...] | None


@dataclass(frozen=True)
class ProtectedReferenceSQL:
    example_id: str
    sql: str
    sql_sha256: str
    path: Path
    condition_cols_variants: tuple[ConditionColumns, ...]
    ignore_order: bool


def _condition_variants(value: Any, example_id: str) -> tuple[ConditionColumns, ...]:
    if value in (None, [], [[]], [None]):
        return (None,)
    if not isinstance(value, list):
        raise EvaluationResourceError(
            "invalid_evaluation_metadata",
            "condition_cols must be a JSON list",
            example_id=example_id,
        )
    if all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return (tuple(value),)
    if all(isinstance(item, list) for item in value):
        variants: list[ConditionColumns] = []
        for item in value:
            if not all(isinstance(index, int) and not isinstance(index, bool) for index in item):
                raise EvaluationResourceError(
                    "invalid_evaluation_metadata",
                    "nested condition_cols must contain only integer indexes",
                    example_id=example_id,
                )
            variants.append(tuple(item) or None)
        return tuple(variants)
    raise EvaluationResourceError(
        "invalid_evaluation_metadata",
        "condition_cols must be a flat list or a list of integer lists",
        example_id=example_id,
    )


class ProtectedReferenceSQLStore:
    """Evaluation-only gold SQL store, intentionally separate from DATA-003."""

    def __init__(
        self,
        references: dict[str, ProtectedReferenceSQL],
        sql_dir: Path,
        evaluation_metadata_path: Path,
        metadata_missing_ids: Iterable[str] = (),
    ) -> None:
        self._references = dict(references)
        self._metadata_missing_ids = frozenset(metadata_missing_ids)
        self.sql_dir = sql_dir.resolve()
        self.evaluation_metadata_path = evaluation_metadata_path.resolve()

    @classmethod
    def from_official_directory(
        cls,
        sql_dir: str | Path,
        evaluation_metadata_jsonl: str | Path,
        *,
        expected_metadata_sha256: str | None = None,
    ) -> "ProtectedReferenceSQLStore":
        directory = Path(sql_dir).expanduser().resolve()
        metadata_path = Path(evaluation_metadata_jsonl).expanduser().resolve()
        if not directory.is_dir():
            raise EvaluationResourceError(
                "reference_directory_not_found",
                "Protected reference SQL directory not found",
                expected_path=str(directory),
            )
        if not metadata_path.is_file():
            raise EvaluationResourceError(
                "evaluation_metadata_not_found",
                "Spider2 evaluation metadata JSONL not found",
                expected_path=str(metadata_path),
            )
        if expected_metadata_sha256 is not None:
            actual_sha256 = sha256_path(metadata_path)
            if actual_sha256 != expected_metadata_sha256:
                raise EvaluationResourceError(
                    "evaluation_metadata_checksum_mismatch",
                    "Spider2 evaluation metadata checksum does not match the pinned protocol",
                    expected_sha256=expected_metadata_sha256,
                    actual_sha256=actual_sha256,
                    path=str(metadata_path),
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

        sql_paths: dict[str, Path] = {}
        for path in sorted(directory.rglob("*.sql")):
            example_id = path.stem
            if example_id in sql_paths:
                raise EvaluationResourceError(
                    "duplicate_reference_id",
                    f"Duplicate reference SQL for {example_id}",
                    example_id=example_id,
                    paths=[str(sql_paths[example_id]), str(path)],
                )
            sql_paths[example_id] = path

        references: dict[str, ProtectedReferenceSQL] = {}
        metadata_missing_ids: list[str] = []
        for example_id, path in sql_paths.items():
            standard = metadata.get(example_id)
            if standard is None:
                metadata_missing_ids.append(example_id)
                continue
            sql = path.read_text(encoding="utf-8").strip()
            if not sql:
                raise EvaluationResourceError(
                    "invalid_reference_sql",
                    f"Reference SQL is empty for {example_id}",
                    example_id=example_id,
                )
            ignore_order = standard.get("ignore_order", False)
            if not isinstance(ignore_order, bool):
                raise EvaluationResourceError(
                    "invalid_evaluation_metadata",
                    "ignore_order must be boolean",
                    example_id=example_id,
                )
            references[example_id] = ProtectedReferenceSQL(
                example_id=example_id,
                sql=sql,
                sql_sha256=sha256_path(path),
                path=path.resolve(),
                condition_cols_variants=_condition_variants(standard.get("condition_cols"), example_id),
                ignore_order=ignore_order,
            )
        return cls(references, directory, metadata_path, metadata_missing_ids)

    def get(self, example_id: str) -> ProtectedReferenceSQL:
        if example_id in self._metadata_missing_ids:
            raise EvaluationResourceError(
                "evaluation_metadata_missing",
                f"Evaluation metadata missing for reference {example_id}",
                example_id=example_id,
            )
        try:
            return self._references[example_id]
        except KeyError as error:
            raise EvaluationResourceError(
                "reference_sql_not_found",
                f"Protected reference SQL not found for {example_id}",
                example_id=example_id,
            ) from error

    @property
    def available_example_ids(self) -> frozenset[str]:
        return frozenset(self._references)

    def missing_example_ids(self, expected_ids: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted(set(expected_ids) - set(self._references)))

    def validate_coverage(self, expected_ids: Iterable[str]) -> None:
        expected = tuple(expected_ids)
        if len(set(expected)) != len(expected):
            raise EvaluationResourceError(
                "duplicate_expected_id",
                "Expected reference coverage contains duplicate IDs",
            )
        missing = sorted(set(expected) - set(self._references))
        if missing:
            raise EvaluationResourceError(
                "reference_coverage_mismatch",
                "Protected reference SQL coverage is incomplete",
                missing=missing,
                missing_count=len(missing),
            )
