from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from text2sql.schema.inspector import inspect_sqlite_schema

from .sql_validator import SQLiteSQLValidator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one read-only SQLite SELECT against a database schema"
    )
    parser.add_argument("--database", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql")
    source.add_argument("--sql-file", type=Path)
    parser.add_argument("--include-ast", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sql = (
        args.sql
        if args.sql is not None
        else args.sql_file.read_text(encoding="utf-8")
    )
    schema = inspect_sqlite_schema(args.database)
    result = SQLiteSQLValidator().validate(sql, schema)
    print(json.dumps(result.to_dict(include_ast=args.include_ast), ensure_ascii=False))
    return 0 if result.valid else 2


if __name__ == "__main__":
    sys.exit(main())
