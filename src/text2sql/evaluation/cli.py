from __future__ import annotations

import argparse
import json
from pathlib import Path

from text2sql.domain import Text2SQLExample

from .evaluator import SQLiteExecutionEvaluator


def _sql_argument(parser: argparse.ArgumentParser, name: str) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(f"--{name}-sql", help=f"{name.title()} SQL text.")
    group.add_argument(f"--{name}-sql-file", type=Path, help=f"UTF-8 file containing {name} SQL.")


def _resolve_sql(text: str | None, path: Path | None) -> str:
    return text if text is not None else path.read_text(encoding="utf-8")  # type: ignore[union-attr]


def _condition_cols(value: str | None) -> tuple[int, ...] | None:
    if value is None or not value.strip():
        return None
    try:
        return tuple(int(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("condition columns must be comma-separated integers") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute and compare generated/reference SQLite SQL.")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--example-id", default="local-fixture-eval")
    parser.add_argument("--db-id", default=None)
    parser.add_argument("--ignore-order", action="store_true")
    parser.add_argument("--condition-cols", default=None, help="Comma-separated reference column indexes.")
    parser.add_argument("--timeout", type=float, default=60.0)
    _sql_argument(parser, "generated")
    _sql_argument(parser, "reference")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        condition_cols = _condition_cols(args.condition_cols)
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))

    example = Text2SQLExample(
        example_id=args.example_id,
        db_id=args.db_id or args.database.stem,
        question="Local EVAL-001 verification",
        dialect="sqlite",
        split="fixture",
    )
    evaluator = SQLiteExecutionEvaluator(timeout_seconds=args.timeout)
    result = evaluator.evaluate(
        example=example,
        database_path=args.database,
        generated_sql=_resolve_sql(args.generated_sql, args.generated_sql_file),
        reference_sql=_resolve_sql(args.reference_sql, args.reference_sql_file),
        condition_cols=condition_cols,
        ignore_order=args.ignore_order,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    if result.correct:
        return 0
    if result.status.endswith("execution_error") or result.status == "comparison_error":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
