from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from text2sql.security import SQL_VALIDATOR_VERSION, SQLValidationResult

SANDBOX_VERSION = "safe002-sqlite-v1"


@dataclass(frozen=True)
class SandboxPolicy:
    timeout_seconds: float = 10.0
    max_result_rows: int = 1_000
    max_result_bytes: int = 1_048_576
    max_value_bytes: int = 262_144
    max_database_bytes: int = 67_108_864
    memory_limit_bytes: int = 268_435_456
    max_sql_characters: int = 100_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        for name, value in asdict(self).items():
            if name != "timeout_seconds" and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")
        if self.memory_limit_bytes < 64 * 1024 * 1024:
            raise ValueError("memory_limit_bytes must be at least 64 MiB")
        if self.max_database_bytes * 2 > self.memory_limit_bytes:
            raise ValueError("Database copies must fit within the memory limit")
        if self.max_value_bytes > self.max_result_bytes:
            raise ValueError("max_value_bytes must not exceed max_result_bytes")


@dataclass(frozen=True)
class SandboxResult:
    status: str
    sql_sha256: str
    duration_ms: int
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    truncated: bool = False
    error_code: str | None = None
    error_message: str | None = None
    database_sha256: str | None = None
    schema_sha256: str | None = None
    validation: dict[str, Any] | None = None
    policy: dict[str, Any] = field(default_factory=dict)
    sandbox_version: str = SANDBOX_VERSION

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "row_count": len(self.rows)}


class SQLiteSandboxExecutor:
    """SAFE-001 validation and bounded execution on a static SQLite snapshot.

    Every worker revalidates SQL against the exact copy it executes. A supplied
    validation result is a checked handoff, never a trusted capability.
    """

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy or SandboxPolicy()

    def execute(
        self, database_path: str | Path, sql: str, validation: SQLValidationResult,
    ) -> SandboxResult:
        sql_hash = _sql_hash(sql)
        if (
            not isinstance(validation, SQLValidationResult)
            or not validation.valid or validation.issues
            or validation.validator_version != SQL_VALIDATOR_VERSION
            or validation.sql_sha256 != sql_hash
        ):
            return SandboxResult(
                sql_sha256=sql_hash, duration_ms=0, policy=asdict(self.policy),
                **_failure("validation_rejected", "invalid_validation_handoff"),
            )
        return self.validate_and_execute(database_path, sql)

    def validate_and_execute(self, database_path: str | Path, sql: str) -> SandboxResult:
        """Perform SAFE-001 inside the bounded worker before executing any SQL."""
        started = time.monotonic()
        sql_hash = _sql_hash(sql)

        def finish(payload: dict[str, Any]) -> SandboxResult:
            return SandboxResult(
                sql_sha256=sql_hash,
                duration_ms=round((time.monotonic() - started) * 1000),
                policy=asdict(self.policy), **payload,
            )

        if not isinstance(sql, str) or not sql.strip() or len(sql) > self.policy.max_sql_characters:
            return finish(_failure("validation_rejected", "invalid_sql"))
        if sys.platform != "linux":
            return finish(_failure("execution_error", "unsupported_platform"))
        request = json.dumps({
            "database_path": str(Path(database_path).absolute()),
            "sql": sql, "policy": asdict(self.policy),
        })
        # The fresh interpreter inherits no application UDFs, connections or
        # provider credentials. It also does not write Python bytecode files.
        env = {
            "PATH": os.defpath,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8",
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "text2sql.execution.worker"],
                input=request.encode("utf-8"), stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env,
                timeout=max(0.001, self.policy.timeout_seconds - (time.monotonic() - started)),
                check=False,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run kills and reaps the child before raising.
            return finish(_failure("timeout", "deadline_exceeded"))
        except OSError:
            return finish(_failure("execution_error", "worker_start_failed"))
        if completed.returncode != 0:
            return finish(_failure("resource_limit", "worker_terminated"))
        try:
            payload = json.loads(completed.stdout)
            payload["columns"] = tuple(payload.get("columns", ()))
            payload["rows"] = tuple(tuple(row) for row in payload.get("rows", ()))
            return finish(payload)
        except (ValueError, TypeError, KeyError, AttributeError):
            return finish(_failure("execution_error", "invalid_worker_response"))


def _sql_hash(sql: object) -> str:
    return hashlib.sha256(sql.encode("utf-8", errors="surrogatepass")).hexdigest() if isinstance(sql, str) else ""


def _failure(status: str, code: str) -> dict[str, Any]:
    # Never echo SQL, literals, file paths or SQLite exception text.
    return {
        "status": status, "error_code": code,
        "error_message": {
            "validation_rejected": "SQL did not pass the sandbox validation boundary.",
            "timeout": "Sandbox deadline exceeded.",
            "resource_limit": "Sandbox resource limit exceeded or worker terminated.",
        }.get(status, "Sandbox could not execute the query."),
    }
