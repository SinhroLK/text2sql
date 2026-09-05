from __future__ import annotations

import argparse
import json
from pathlib import Path

from text2sql.schema import inspect_sqlite_schema

from .semantic_plan import (
    SemanticPlanResolutionError,
    resolve_semantic_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and hash one provider-free SEM-002 semantic plan."
    )
    parser.add_argument("--plan", required=True, type=Path, help="Semantic plan JSON")
    parser.add_argument(
        "--database", required=True, type=Path, help="SQLite database path"
    )
    parser.add_argument("--question", required=True, help="Exact source question")
    parser.add_argument("--db-id", default=None, help="Optional database identifier")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        schema = inspect_sqlite_schema(args.database, db_id=args.db_id)
        raw_response = args.plan.read_text(encoding="utf-8")
        result = resolve_semantic_plan(
            raw_response,
            schema,
            expected_question=args.question,
        )
    except (OSError, UnicodeError, ValueError) as error:
        payload: dict[str, object] = {
            "status": "error",
            "error_type": type(error).__name__,
            "message": str(error),
        }
        if isinstance(error, SemanticPlanResolutionError):
            payload["attempts"] = error.attempts
            payload["issues"] = [issue.to_dict() for issue in error.issues]
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(
        json.dumps(
            {"status": "valid", **result.to_dict()},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
