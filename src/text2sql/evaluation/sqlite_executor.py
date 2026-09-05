from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .models import QueryExecutionResult


# Authorizer action codes deny side effects during preparation, before execution.
_READ_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE,
})


def _authorize_read_only(
    action: int,
    argument1: str | None,
    argument2: str | None,
    database: str | None,
    trigger: str | None,
) -> int:
    if action not in _READ_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (argument2 or "").casefold() in {
        "load_extension", "writefile", "readfile",
    }:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class SQLiteQueryExecutor:
    """Execute one result-producing SQL statement in an isolated SQLite copy."""

    def __init__(self, timeout_seconds: float = 60.0, progress_operations: int = 1_000) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if progress_operations <= 0:
            raise ValueError("progress_operations must be positive")
        self.timeout_seconds = timeout_seconds
        self.progress_operations = progress_operations

    def execute(self, database_path: str | Path, sql: str) -> QueryExecutionResult:
        started = time.perf_counter()
        path = Path(database_path).expanduser().resolve()
        if not path.is_file():
            return self._error(started, "database_not_found", f"SQLite database not found: {path}")
        if not isinstance(sql, str) or not sql.strip():
            return self._error(started, "invalid_sql", "SQL must be a non-empty string")

        source_connection: sqlite3.Connection | None = None
        memory_connection: sqlite3.Connection | None = None
        timed_out = False
        try:
            source_connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
            memory_connection = sqlite3.connect(":memory:")
            source_connection.backup(memory_connection)
            source_connection.close()
            source_connection = None

            memory_connection.execute("PRAGMA query_only = ON")
            memory_connection.set_authorizer(_authorize_read_only)
            deadline = time.monotonic() + self.timeout_seconds

            def stop_after_deadline() -> int:
                nonlocal timed_out
                if time.monotonic() >= deadline:
                    timed_out = True
                    return 1
                return 0

            memory_connection.set_progress_handler(stop_after_deadline, self.progress_operations)
            cursor = memory_connection.execute(sql)
            if cursor.description is None:
                return self._error(started, "non_result_statement", "SQL statement did not return a result set")
            columns = tuple(str(description[0]) for description in cursor.description)
            rows = tuple(tuple(row) for row in cursor.fetchall())
            return QueryExecutionResult(
                status="success",
                columns=columns,
                rows=rows,
                row_count=len(rows),
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        except sqlite3.Error as error:
            error_type = "timeout" if timed_out else type(error).__name__
            status = "timeout" if timed_out else "execution_error"
            return QueryExecutionResult(
                status=status,
                columns=(),
                rows=(),
                row_count=0,
                duration_ms=round((time.perf_counter() - started) * 1000),
                error_type=error_type,
                error_message=str(error),
            )
        finally:
            if memory_connection is not None:
                memory_connection.set_progress_handler(None, 0)
                memory_connection.close()
            if source_connection is not None:
                source_connection.close()

    @staticmethod
    def _error(started: float, error_type: str, message: str) -> QueryExecutionResult:
        return QueryExecutionResult(
            status="execution_error",
            columns=(),
            rows=(),
            row_count=0,
            duration_ms=round((time.perf_counter() - started) * 1000),
            error_type=error_type,
            error_message=message,
        )
