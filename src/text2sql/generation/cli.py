from __future__ import annotations

import argparse
import json
from pathlib import Path

from text2sql.planning import resolve_semantic_plan
from text2sql.retrieval import (
    QuestionPlanHybridSelector,
    load_structural_retrieval_config,
    load_verified_retrieval_index,
    load_verified_structural_index,
)
from text2sql.schema import inspect_sqlite_schema

from .b7p import B7PComposer, load_b7p_composer_config


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compose and audit a frozen GEN-001/B7P prompt without calling a provider."
        )
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--db-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs/generation/gen001-b7p-composer-v1.toml",
    )
    parser.add_argument(
        "--source-index",
        type=Path,
        default=PROJECT_ROOT
        / "artifacts/retrieval/spider1-train-v1/retrieval-index.jsonl",
    )
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=PROJECT_ROOT
        / "artifacts/retrieval/spider1-train-v1/retrieval-manifest.json",
    )
    parser.add_argument(
        "--expected-source-manifest",
        type=Path,
        default=PROJECT_ROOT
        / "configs/datasets/spider1-train-retrieval-manifest-v1.json",
    )
    parser.add_argument(
        "--structural-index",
        type=Path,
        default=PROJECT_ROOT
        / "artifacts/retrieval/spider1-train-structural-v1/structural-index.jsonl",
    )
    parser.add_argument(
        "--structural-manifest",
        type=Path,
        default=PROJECT_ROOT
        / "artifacts/retrieval/spider1-train-structural-v1/structural-manifest.json",
    )
    parser.add_argument("--prompt-output", type=Path)
    parser.add_argument("--audit-output", type=Path)
    return parser


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_b7p_composer_config(args.config)
    structural_config = load_structural_retrieval_config(
        config.structural_config_path
    )
    source = load_verified_retrieval_index(
        args.source_index,
        args.source_manifest,
        expected_manifest_path=args.expected_source_manifest,
    )
    structural = load_verified_structural_index(
        args.structural_index,
        args.structural_manifest,
        source,
        structural_config,
        expected_manifest_path=config.structural_manifest_path,
    )
    schema = inspect_sqlite_schema(args.database, db_id=args.db_id)
    plan = resolve_semantic_plan(
        args.plan.read_text(encoding="utf-8"),
        schema,
        expected_question=args.question,
        expected_plan_version=config.semantic_plan_version,
    )
    composition = B7PComposer(
        config, QuestionPlanHybridSelector(structural, structural_config)
    ).compose(args.question, args.database, plan, db_id=args.db_id)
    audit = {
        "status": "composed_offline",
        **composition.to_audit_dict(),
        "prompt_output": str(args.prompt_output) if args.prompt_output else None,
    }
    rendered_audit = json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.prompt_output is not None:
        _write(args.prompt_output, composition.prompt)
    if args.audit_output is not None:
        _write(args.audit_output, rendered_audit)
    print(rendered_audit, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
