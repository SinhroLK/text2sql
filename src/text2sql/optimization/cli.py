from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from text2sql.datasets import load_and_validate_protocol, load_spider2_lite_sqlite
from text2sql.evaluation import (
    EvaluationResourceError,
    OfficialGoldResultStore,
    Spider2GoldResultRunner,
    Spider2SQLiteDatabaseResolver,
)
from text2sql.retrieval import load_verified_retrieval_index

from .b5 import (
    B5RunError,
    optimize_b5,
    prepare_b5_dataset,
    run_b5,
    validate_b5_runtime_dependencies,
)
from .config import B5ConfigurationError, load_b5_optimization_config
from .recovery import B5RecoveryError


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OPTIMIZATION_CONFIG = (
    PROJECT_ROOT / "configs/optimization/dspy001-b5.toml"
)
DEFAULT_DATASET_CONFIG = (
    PROJECT_ROOT / "configs/datasets/spider2-lite-sqlite-v1.toml"
)
DEFAULT_DATASET_MANIFEST = (
    PROJECT_ROOT
    / "configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json"
)
DEFAULT_SOURCE = (
    PROJECT_ROOT / "data/raw/spider2/spider2-lite/spider2-lite.jsonl"
)
DEFAULT_DATABASE_ROOT = (
    PROJECT_ROOT
    / "data/raw/spider2/spider2-lite/resource/databases/spider2-localdb"
)
DEFAULT_GOLD_RESULT_ROOT = (
    PROJECT_ROOT
    / "data/raw/spider2/spider2-lite/evaluation_suite/gold/exec_result"
)
DEFAULT_STANDARDS = (
    PROJECT_ROOT
    / "data/raw/spider2/spider2-lite/evaluation_suite/gold/spider2lite_eval.jsonl"
)
DEFAULT_RETRIEVAL_INDEX = (
    PROJECT_ROOT
    / "artifacts/retrieval/spider1-train-v1/retrieval-index.jsonl"
)
DEFAULT_RETRIEVAL_MANIFEST = (
    PROJECT_ROOT
    / "artifacts/retrieval/spider1-train-v1/retrieval-manifest.json"
)
DEFAULT_EXPECTED_RETRIEVAL_MANIFEST = (
    PROJECT_ROOT
    / "configs/datasets/spider1-train-retrieval-manifest-v1.json"
)
DEFAULT_PROGRAM = (
    PROJECT_ROOT / "artifacts/dspy/dspy001-b5/program-state.json"
)
DEFAULT_OPTIMIZATION_MANIFEST = (
    PROJECT_ROOT / "artifacts/dspy/dspy001-b5/optimization-manifest.json"
)
DEFAULT_CHECKPOINT_ROOT = (
    PROJECT_ROOT / "artifacts/dspy/dspy001-b5/checkpoints"
)


def _add_resource_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--optimization-config",
        type=Path,
        default=DEFAULT_OPTIMIZATION_CONFIG,
    )
    parser.add_argument("--source-jsonl", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dataset-config", type=Path, default=DEFAULT_DATASET_CONFIG)
    parser.add_argument(
        "--expected-dataset-manifest",
        type=Path,
        default=DEFAULT_DATASET_MANIFEST,
    )
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--retrieval-index", type=Path, default=DEFAULT_RETRIEVAL_INDEX)
    parser.add_argument(
        "--retrieval-manifest",
        type=Path,
        default=DEFAULT_RETRIEVAL_MANIFEST,
    )
    parser.add_argument(
        "--expected-retrieval-manifest",
        type=Path,
        default=DEFAULT_EXPECTED_RETRIEVAL_MANIFEST,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit or compile the frozen DSPY-001/B5 program."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser(
        "audit",
        help="Prepare and verify B5 inputs without provider calls.",
    )
    _add_resource_arguments(audit)
    audit.add_argument("--report", type=Path)

    optimize = commands.add_parser(
        "optimize",
        help="Run paid MIPROv2 compilation and freeze its program artifact.",
    )
    _add_resource_arguments(optimize)
    optimize.add_argument(
        "--gold-result-root", type=Path, default=DEFAULT_GOLD_RESULT_ROOT
    )
    optimize.add_argument("--standards-jsonl", type=Path, default=DEFAULT_STANDARDS)
    optimize.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    optimize.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_OPTIMIZATION_MANIFEST,
    )

    optimize.add_argument(
        "--checkpoint-root", type=Path, default=DEFAULT_CHECKPOINT_ROOT
    )
    optimize.add_argument(
        "--resume-run-id",
        help="Explicitly continue one compatible interrupted optimization run.",
    )
    run = commands.add_parser(
        "run",
        help="Run a frozen B5 over all 31 development examples and score it.",
    )
    _add_resource_arguments(run)
    run.add_argument(
        "--gold-result-root", type=Path, default=DEFAULT_GOLD_RESULT_ROOT
    )
    run.add_argument("--standards-jsonl", type=Path, default=DEFAULT_STANDARDS)
    run.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    run.add_argument(
        "--manifest", type=Path, default=DEFAULT_OPTIMIZATION_MANIFEST
    )
    run.add_argument(
        "--predictions",
        type=Path,
        default=PROJECT_ROOT / "artifacts/experiments/dspy001-b5-predictions.jsonl",
    )
    run.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "artifacts/reports/dspy001-b5-report.json",
    )
    return parser


def _load_inputs(args: argparse.Namespace) -> tuple[Any, Any, Any, Any]:
    config = load_b5_optimization_config(args.optimization_config)
    validate_b5_runtime_dependencies(config)
    dataset = load_spider2_lite_sqlite(
        args.source_jsonl,
        args.dataset_config,
        PROJECT_ROOT,
        args.expected_dataset_manifest,
    )
    retrieval = load_verified_retrieval_index(
        args.retrieval_index,
        args.retrieval_manifest,
        expected_manifest_path=args.expected_retrieval_manifest,
    )
    resolver = Spider2SQLiteDatabaseResolver(args.database_root)
    prepared = prepare_b5_dataset(
        config=config,
        dataset=dataset,
        retrieval_index=retrieval,
        database_resolver=resolver,
    )
    return config, dataset, resolver, prepared


def _audit_payload(config: Any, prepared: Any) -> dict[str, Any]:
    examples = [
        {
            "example_id": item.example_id,
            "db_id": item.db_id,
            "prompt_sha256": item.prompt_sha256,
            "retrieval_ids": [
                selected["retrieval_id"]
                for selected in item.retrieval_audit["selected"]
            ],
        }
        for item in prepared.all_examples
    ]
    return {
        "schema_version": 1,
        "status": "ready",
        "optimization_id": config.optimization_id,
        "config_sha256": config.config_sha256,
        "runtime_dependencies": validate_b5_runtime_dependencies(config),
        "base_b4_config_sha256": config.base_config_sha256,
        "provider_calls": 0,
        "spider2_gold_sql_used": False,
        "test_examples_used": 0,
        "train_database_ids": list(config.train_database_ids),
        "validation_database_ids": list(config.validation_database_ids),
        "train_examples": len(prepared.train),
        "validation_examples": len(prepared.validation),
        "rate_limit": {
            "tokens_per_minute": config.tokens_per_minute,
            "token_safety_margin": config.token_safety_margin,
            "safe_token_budget": int(
                config.tokens_per_minute * config.token_safety_margin
            ),
            "window_seconds": config.rate_limit_window_seconds,
            "buffer_seconds": config.rate_limit_buffer_seconds,
            "max_rate_limit_retries": config.rate_limit_max_retries,
        },
        "recovery_cache": {
            "enabled": config.cache,
            "schema_version": config.cache_schema_version,
            "scope": "single-explicit-run",
            "size_limit_bytes": config.cache_size_limit_bytes,
            "resume_max_age_hours": config.cache_resume_max_age_hours,
            "independent_runs_reuse_cache": False,
        },
        "examples": examples,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config, dataset, resolver, prepared = _load_inputs(args)
        if args.command == "audit":
            result = _audit_payload(config, prepared)
            if args.report is not None:
                _write_json(args.report, result)
        else:
            protocol, _ = load_and_validate_protocol(
                args.dataset_config, PROJECT_ROOT
            )
            development_ids = {
                item.example_id for item in prepared.all_examples
            }
            gold_results = OfficialGoldResultStore.from_official_directory(
                args.gold_result_root,
                args.standards_jsonl,
                expected_metadata_sha256=protocol["source"][
                    "evaluation_manifest_sha256"
                ],
                allowed_example_ids=development_ids,
            )
            evaluator = Spider2GoldResultRunner(
                dataset=dataset,
                database_resolver=resolver,
                gold_results=gold_results,
            )
            if args.command == "optimize":
                result = optimize_b5(
                    config=config,
                    prepared=prepared,
                    evaluator=evaluator,
                    program_path=args.program,
                    manifest_path=args.manifest,
                    checkpoint_root=args.checkpoint_root,
                    resume_run_id=args.resume_run_id,
                )
            else:
                result = run_b5(
                    config=config,
                    prepared=prepared,
                    evaluator=evaluator,
                    program_path=args.program,
                    manifest_path=args.manifest,
                    predictions_path=args.predictions,
                    report_path=args.report,
                )
    except (
        B5ConfigurationError,
        B5RunError,
        EvaluationResourceError,
        FileNotFoundError,
        RuntimeError,
        B5RecoveryError,
        ValueError,
    ) as error:
        result = {
            "status": "error",
            "error_type": type(error).__name__,
            "message": str(error),
        }
        if isinstance(error, B5RunError):
            result["code"] = error.code
            result["context"] = error.context
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
