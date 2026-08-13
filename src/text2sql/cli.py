from __future__ import annotations

import argparse
import json
from pathlib import Path

from text2sql.observability import append_jsonl
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import MockSchemaAwareProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate SQL from a question and a local SQLite schema."
    )
    parser.add_argument("--question", required=True, help="Natural-language question")
    parser.add_argument("--database", required=True, type=Path, help="SQLite database path")
    parser.add_argument("--db-id", default=None, help="Optional database identifier")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSONL output path")
    parser.add_argument(
        "--provider",
        choices=("mock",),
        default="mock",
        help="Phase 0 supports only the deterministic mock provider",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    provider = MockSchemaAwareProvider()
    pipeline = Text2SQLPipeline(provider)
    result = pipeline.generate(args.question, args.database, db_id=args.db_id)
    record = result.to_dict()

    if args.output is not None:
        append_jsonl(args.output, record)

    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

