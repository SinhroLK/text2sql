from __future__ import annotations

import argparse
import json
from pathlib import Path

from text2sql.observability import append_jsonl
from text2sql.datasets.documents import TaskDocumentStore
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import GroqProvider, MockSchemaAwareProvider


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
        choices=("mock", "groq"),
        default="mock",
        help="Provider to use; Groq requires GROQ_API_KEY",
    )
    parser.add_argument("--model-id", help="Required when --provider groq is selected")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high"))
    parser.add_argument("--document-root", type=Path)
    parser.add_argument("--document-manifest", type=Path)
    parser.add_argument("--external-knowledge", help="Pinned task-document filename")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    document_args = (args.document_root, args.document_manifest, args.external_knowledge)
    if any(item is not None for item in document_args) and not all(
        item is not None for item in document_args
    ):
        build_parser().error("Document root, manifest and external-knowledge must be supplied together")
    documents = (
        TaskDocumentStore.from_manifest(args.document_root, args.document_manifest).load(
            args.external_knowledge
        ) if args.external_knowledge is not None else ()
    )
    if args.provider == "groq":
        if not args.model_id:
            build_parser().error("--model-id is required when --provider groq is selected")
        provider = GroqProvider(
            model_id=args.model_id,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            seed=args.seed,
            **({"reasoning_effort": args.reasoning_effort} if args.reasoning_effort else {}),
        )
    else:
        provider = MockSchemaAwareProvider()
    pipeline = Text2SQLPipeline(provider)
    result = pipeline.generate(args.question, args.database, db_id=args.db_id, documents=documents)
    record = result.to_dict()

    if args.output is not None:
        append_jsonl(args.output, record)

    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.selected_sql is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
