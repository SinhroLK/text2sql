from __future__ import annotations

import argparse
import json
from pathlib import Path

from .sandbox import SandboxPolicy, SQLiteSandboxExecutor, _failure


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and sandbox one SQLite SELECT")
    parser.add_argument("--database", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql")
    source.add_argument("--sql-file", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--max-result-rows", type=int, default=1_000)
    args = parser.parse_args(argv)
    try:
        policy = SandboxPolicy(timeout_seconds=args.timeout_seconds, max_result_rows=args.max_result_rows)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        if args.sql is not None:
            sql = args.sql
        else:
            with args.sql_file.open(encoding="utf-8") as source_file:
                sql = source_file.read(policy.max_sql_characters + 1)
        result = SQLiteSandboxExecutor(policy).validate_and_execute(args.database, sql)
        payload = result.to_dict()
    except (OSError, ValueError):
        payload = _failure("execution_error", "input_unavailable")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if payload["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
