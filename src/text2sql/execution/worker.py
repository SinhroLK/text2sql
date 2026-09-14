"""Internal SAFE-002 worker. No application UDFs or extensions are loaded."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import resource
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any

from .sandbox import SandboxPolicy, _failure


def _limit_resources(policy: SandboxPolicy) -> None:
    for kind, requested in (
        (resource.RLIMIT_AS, policy.memory_limit_bytes),
        (resource.RLIMIT_CPU, max(1, math.ceil(policy.timeout_seconds))),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_CORE, 0),
    ):
        _, hard = resource.getrlimit(kind)
        bound = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
        resource.setrlimit(kind, (bound, bound))


def _json_cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"type": "blob", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"type": "float", "value": str(value)}
    return value


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _has_sidecars(path: Path) -> bool:
    return any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


def _execute(request: dict[str, Any], policy: SandboxPolicy) -> dict[str, Any]:
    from text2sql.schema.canonical import canonical_schema_sha256
    from text2sql.schema.inspector import inspect_sqlite_connection
    from text2sql.security import SQLiteSQLValidator, SQLiteValidationPolicy
    from text2sql.security.sql_validator import _AuthorizationAudit

    path = Path(request["database_path"]).resolve()
    # Read one bounded byte image, never open the source with SQLite. Even a
    # read-only WAL connection may require sidecars beside the original file.
    if _has_sidecars(path):
        return _failure("execution_error", "standalone_snapshot_required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            return _failure("execution_error", "regular_database_required")
        if before.st_size > policy.max_database_bytes:
            return _failure("resource_limit", "database_size_limit")
        data = source.read(policy.max_database_bytes + 1)
        after = os.fstat(source.fileno())
    if len(data) > policy.max_database_bytes:
        return _failure("resource_limit", "database_size_limit")
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ) or _has_sidecars(path):
        return _failure("execution_error", "database_changed")
    if len(data) < 100 or data[:16] != b"SQLite format 3\x00":
        return _failure("execution_error", "invalid_database")
    if data[18:20] != b"\x01\x01":
        return _failure("execution_error", "standalone_snapshot_required")
    database_hash = hashlib.sha256(data).hexdigest()
    connection = sqlite3.connect(":memory:")
    try:
        if not hasattr(connection, "deserialize"):
            return _failure("execution_error", "deserialize_unavailable")
        connection.deserialize(data)
        del data
        if hasattr(connection, "enable_load_extension"):
            connection.enable_load_extension(False)
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA query_only = ON")
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, policy.max_value_bytes)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, policy.max_sql_characters * 4)
        # Virtual tables can have module-specific behavior; admit ordinary
        # tables only. Canonical inspection already excludes views.
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND upper(sql) LIKE 'CREATE VIRTUAL TABLE%' LIMIT 1"
        ).fetchone():
            return _failure("execution_error", "virtual_tables_not_supported")
        schema = inspect_sqlite_connection(connection, "sandbox")
        validation = SQLiteSQLValidator(SQLiteValidationPolicy(
            max_sql_characters=policy.max_sql_characters
        )).validate(request["sql"], schema)
        audit = {
            "database_sha256": database_hash,
            "schema_sha256": canonical_schema_sha256(schema),
            "validation": {
                "valid": validation.valid,
                "validator_version": validation.validator_version,
                "sql_sha256": validation.sql_sha256,
                "ast_sha256": validation.ast_sha256,
                "issue_codes": [issue.code for issue in validation.issues],
            },
        }
        if not validation.valid:
            return {**_failure("validation_rejected", "snapshot_validation_failed"), **audit}
        authorization = _AuthorizationAudit({table.name.casefold(): table.name for table in schema.tables})
        connection.set_authorizer(authorization.authorize)
        try:
            cursor = connection.execute(request["sql"])
            columns = [item[0] for item in cursor.description]
            rows: list[list[Any]] = []
            size = _encoded_size({"columns": columns, "rows": []})
            if size > policy.max_result_bytes:
                return {**_failure("resource_limit", "result_bytes_limit"), **audit}
            truncated = False
            reason = None
            for raw in cursor:
                if len(rows) == policy.max_result_rows:
                    truncated, reason = True, "result_rows_limit"
                    break
                row = [_json_cell(cell) for cell in raw]
                row_size = _encoded_size(row) + bool(rows)
                if size + row_size > policy.max_result_bytes:
                    truncated, reason = True, "result_bytes_limit"
                    break
                rows.append(row)
                size += row_size
            return {
                "status": "truncated" if truncated else "success",
                "columns": columns, "rows": rows, "truncated": truncated,
                "error_code": reason, **audit,
            }
        except sqlite3.DataError:
            return {**_failure("resource_limit", "value_size_limit"), **audit}
        except sqlite3.Error as exc:
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_NOMEM:
                return {**_failure("resource_limit", "memory_limit"), **audit}
            return {**_failure("execution_error", "query_execution_failed"), **audit}
    finally:
        connection.close()


def main() -> None:
    try:
        request = json.loads(sys.stdin.buffer.read())
        policy = SandboxPolicy(**request["policy"])
        _limit_resources(policy)
        payload = _execute(request, policy)
    except MemoryError:
        payload = _failure("resource_limit", "memory_limit")
    except FileNotFoundError:
        payload = _failure("execution_error", "database_not_found")
    except Exception:
        payload = _failure("execution_error", "sandbox_setup_failed")
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
