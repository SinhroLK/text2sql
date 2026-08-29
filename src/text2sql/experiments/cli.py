from __future__ import annotations

import argparse
import json
from pathlib import Path

from text2sql.datasets import load_spider2_lite_sqlite
from text2sql.evaluation import (
    EvaluationResourceError,
    OfficialGoldResultStore,
    Spider2GoldResultRunner,
    Spider2SQLiteDatabaseResolver,
)
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import GroqProvider, GroqProviderError

from .config import ExperimentConfigurationError, load_baseline_config
from .runner import BaselineExperimentRunner, ExperimentRunError


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET_CONFIG = PROJECT_ROOT / "configs/datasets/spider2-lite-sqlite-v1.toml"
DEFAULT_DATASET_MANIFEST = PROJECT_ROOT / "configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json"
DEFAULT_SOURCE = PROJECT_ROOT / "data/raw/spider2/spider2-lite/spider2-lite.jsonl"
DEFAULT_DATABASE_ROOT = PROJECT_ROOT / "data/raw/spider2/spider2-lite/resource/databases/spider2-localdb"
DEFAULT_GOLD_RESULT_ROOT = PROJECT_ROOT / "data/raw/spider2/spider2-lite/evaluation_suite/gold/exec_result"
DEFAULT_STANDARDS = PROJECT_ROOT / "data/raw/spider2/spider2-lite/evaluation_suite/gold/spider2lite_eval.jsonl"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a resumable EXP-001 B0 or B1 development baseline."
    )
    parser.add_argument("--experiment-config", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--source-jsonl", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dataset-config", type=Path, default=DEFAULT_DATASET_CONFIG)
    parser.add_argument(
        "--expected-dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST
    )
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--gold-result-root", type=Path, default=DEFAULT_GOLD_RESULT_ROOT)
    parser.add_argument("--standards-jsonl", type=Path, default=DEFAULT_STANDARDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_baseline_config(args.experiment_config)
        dataset = load_spider2_lite_sqlite(
            args.source_jsonl,
            args.dataset_config,
            PROJECT_ROOT,
            args.expected_dataset_manifest,
        )
        resolver = Spider2SQLiteDatabaseResolver(args.database_root)
        evaluator = Spider2GoldResultRunner(
            dataset=dataset,
            database_resolver=resolver,
            gold_results=OfficialGoldResultStore.from_official_directory(
                args.gold_result_root, args.standards_jsonl
            ),
        )
        provider = GroqProvider(
            model_id=config.model_id,
            temperature=config.temperature,
            reasoning_effort=config.reasoning_effort,
            max_tokens=config.max_tokens,
            seed=config.seed,
            max_retries=config.max_retries,
            timeout_seconds=config.timeout_seconds,
        )
        runner = BaselineExperimentRunner(
            config=config,
            dataset=dataset,
            pipeline=Text2SQLPipeline(provider),
            evaluator=evaluator,
        )
        result = runner.run(args.predictions, args.report)
    except (
        EvaluationResourceError,
        ExperimentConfigurationError,
        ExperimentRunError,
        FileNotFoundError,
        GroqProviderError,
        ValueError,
    ) as error:
        payload = {
            "status": "error",
            "error_type": type(error).__name__,
            "message": str(error),
        }
        if isinstance(error, ExperimentRunError):
            payload["code"] = error.code
            payload["context"] = error.context
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
