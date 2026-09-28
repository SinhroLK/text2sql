from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from text2sql.datasets import (
    load_and_validate_protocol,
    load_spider2_lite_sqlite,
)
from text2sql.evaluation import (
    EvaluationResourceError,
    OfficialGoldResultStore,
    Spider2GoldResultRunner,
    Spider2SQLiteDatabaseResolver,
)
from text2sql.experiments import ExperimentConfigurationError, ExperimentRunError
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import GroqProvider, GroqProviderError
from text2sql.retrieval import (
    RetrievalIndexError,
    load_verified_retrieval_index,
)

from .freeze_v2 import FINAL_ARMS, verify_freeze_manifest
from .runner_v2 import (
    FinalExperimentGenerator,
    artifact_record,
    build_final_report,
    load_final_config,
    write_json_immutable,
)
from .statistics import (
    AnalysisError,
    analyze_reports,
    load_arm_report,
    write_analysis,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts/final/run002-v2"
DEFAULT_MANIFEST = PROJECT_ROOT / "configs/final/run001-freeze-v2.json"
DEFAULT_PLAN = PROJECT_ROOT / "configs/final/statistical-plan-v2.toml"
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
    / "data/raw/spider2/spider2-lite/evaluation_suite/gold/"
    "spider2lite_eval.jsonl"
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


class FinalRunError(RuntimeError):
    pass


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise FinalRunError(
            f"Git release-gate command failed: git {' '.join(arguments)}"
        )
    return result.stdout.strip()


def verify_release_gate() -> tuple[dict[str, Any], str]:
    manifest = verify_freeze_manifest(PROJECT_ROOT, DEFAULT_MANIFEST)
    status = _git("status", "--porcelain")
    if status:
        raise FinalRunError(
            "RUN-002 requires a clean working tree; commit or remove changes"
        )
    head = _git("rev-parse", "HEAD")
    tagged = _git("rev-list", "-n", "1", manifest["release_tag"])
    if head != tagged:
        raise FinalRunError(
            f"HEAD must carry release tag {manifest['release_tag']}"
        )
    return manifest, head


def _build_resources(args: argparse.Namespace) -> tuple[Any, Any, Any, Any]:
    protocol, _ = load_and_validate_protocol(
        args.dataset_config, PROJECT_ROOT
    )
    dataset = load_spider2_lite_sqlite(
        args.source_jsonl,
        args.dataset_config,
        PROJECT_ROOT,
        args.expected_dataset_manifest,
    )
    test_examples = dataset.for_split("test")
    test_ids = {item.example_id for item in test_examples}
    if len(test_examples) != 104 or len(test_ids) != 104:
        raise FinalRunError(
            "Frozen dataset does not contain exactly 104 unique test examples"
        )
    if len({item.db_id for item in test_examples}) != 24:
        raise FinalRunError(
            "Frozen test split does not contain exactly 24 databases"
        )
    resolver = Spider2SQLiteDatabaseResolver(args.database_root)
    gold = OfficialGoldResultStore.from_official_directory(
        args.gold_result_root,
        args.standards_jsonl,
        expected_metadata_sha256=protocol["source"][
            "evaluation_manifest_sha256"
        ],
        allowed_example_ids=test_ids,
    )
    evaluator = Spider2GoldResultRunner(
        dataset=dataset,
        database_resolver=resolver,
        gold_results=gold,
    )
    preflight = evaluator.preflight(split="test")
    if (
        not preflight.ready
        or preflight.expected_examples != 104
        or preflight.expected_databases != 24
    ):
        raise FinalRunError(
            f"Held-out resource preflight is not ready: {preflight.to_dict()}"
        )
    retrieval = load_verified_retrieval_index(
        args.retrieval_index,
        args.retrieval_manifest,
        expected_manifest_path=args.expected_retrieval_manifest,
    )
    return dataset, resolver, evaluator, retrieval


def _paths(output_root: Path, arm: str) -> tuple[Path, Path]:
    stem = f"final002-{arm.casefold()}-test"
    return (
        output_root / "predictions" / f"{stem}-predictions.jsonl",
        output_root / "reports" / f"{stem}-report.json",
    )


def _emit(event: str, **details: Any) -> None:
    print(json.dumps({"event": event, **details}, sort_keys=True), flush=True)


def _emit_operational(arm: str, record: dict[str, Any]) -> None:
    _emit(
        f"run002_v2_{record['event']}",
        arm=arm,
        example_id=record["example_id"],
        category=record.get("category"),
        status_code=record.get("status_code"),
        retry_count=record.get("retry_count"),
        retry_after_seconds=record.get("retry_after_seconds"),
    )


def _preflight_payload(
    *,
    freeze: dict[str, Any],
    release_commit: str,
    evaluator: Spider2GoldResultRunner,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": "run002-final-evaluation-v2",
        "freeze_id": freeze["freeze_id"],
        "release_tag": freeze["release_tag"],
        "release_commit": release_commit,
        "source_tree_sha256": freeze["source_tree_sha256"],
        "status": "ready",
        "held_out_examples": 104,
        "held_out_databases": 24,
        "evaluation_resources": evaluator.resource_manifest(split="test"),
    }


def _compare_or_write_analysis(
    analysis: dict[str, Any],
    *,
    json_path: Path,
    csv_path: Path,
) -> None:
    if not json_path.exists() and not csv_path.exists():
        write_analysis(
            analysis, json_path=json_path, csv_path=csv_path
        )
        return
    if not json_path.is_file() or not csv_path.is_file():
        raise FinalRunError(
            "Final analysis is partial; existing outputs are immutable"
        )
    expected_json = json.dumps(
        analysis, indent=2, sort_keys=True
    ) + "\n"
    if json_path.read_text(encoding="utf-8") != expected_json:
        raise FinalRunError("Existing final analysis JSON does not reproduce")
    with tempfile.TemporaryDirectory() as directory:
        temporary_json = Path(directory) / "analysis.json"
        temporary_csv = Path(directory) / "analysis.csv"
        write_analysis(
            analysis,
            json_path=temporary_json,
            csv_path=temporary_csv,
        )
        if csv_path.read_bytes() != temporary_csv.read_bytes():
            raise FinalRunError(
                "Existing final analysis CSV does not reproduce"
            )


def execute(args: argparse.Namespace) -> dict[str, Any]:
    freeze, release_commit = verify_release_gate()
    output_root = args.output_root.expanduser().resolve()
    dataset, resolver, evaluator, retrieval = _build_resources(args)
    preflight_path = output_root / "preflight.json"
    preflight_payload = _preflight_payload(
        freeze=freeze,
        release_commit=release_commit,
        evaluator=evaluator,
    )
    write_json_immutable(preflight_path, preflight_payload)
    _emit(
        "run002_v2_preflight_ready",
        examples=104,
        databases=24,
        output_root=output_root.as_posix(),
    )
    if args.preflight_only:
        return preflight_payload

    if not os.environ.get("GROQ_API_KEY"):
        raise FinalRunError(
            "GROQ_API_KEY is required after successful offline preflight"
        )

    checkpoints: dict[str, dict[str, dict[str, Any]]] = {}
    configs = {
        arm: load_final_config(PROJECT_ROOT, arm) for arm in FINAL_ARMS
    }
    for arm in FINAL_ARMS:
        predictions_path, _ = _paths(output_root, arm)
        config = configs[arm]
        _emit(
            "run002_v2_generation_start",
            arm=arm,
            predictions=predictions_path.as_posix(),
        )
        provider = GroqProvider(
            model_id=config.model_id,
            temperature=config.temperature,
            reasoning_effort=config.reasoning_effort,
            max_tokens=config.max_tokens,
            seed=config.seed,
            max_retries=0,
            timeout_seconds=config.timeout_seconds,
        )
        generator = FinalExperimentGenerator(
            config=config,
            dataset=dataset,
            pipeline=Text2SQLPipeline(provider),
            database_resolver=resolver,
            retrieval_index=retrieval if arm == "B4" else None,
            request_delay_seconds=args.request_delay_seconds,
            transient_retry_seconds=args.transient_retry_seconds,
            progress=lambda example_id, completed, status, arm=arm: _emit(
                "run002_v2_attempt_recorded",
                arm=arm,
                example_id=example_id,
                completed=completed,
                total=104,
                status=status,
            ),
            operational_progress=lambda record, arm=arm: _emit_operational(
                arm, record
            ),
        )
        checkpoints[arm] = generator.generate(predictions_path)
        _emit(
            "run002_v2_generation_complete",
            arm=arm,
            attempts=len(checkpoints[arm]),
            provider_failures=sum(
                item["generation"].get("error_category") is not None
                for item in checkpoints[arm].values()
            ),
        )

    if any(len(checkpoints[arm]) != 104 for arm in FINAL_ARMS):
        raise FinalRunError(
            "Scoring gate requires complete 104-example checkpoints for all arms"
        )
    _emit("run002_v2_all_generations_complete", arms=list(FINAL_ARMS))

    expected_ids = tuple(
        sorted(item.example_id for item in dataset.for_split("test"))
    )
    for arm in FINAL_ARMS:
        predictions_path, report_path = _paths(output_root, arm)
        if report_path.exists():
            load_arm_report(
                report_path,
                arm=arm,
                expected_ids=expected_ids,
                expected_config_sha256=configs[arm].config_sha256,
                expected_experiment_id=configs[arm].experiment_id,
            )
            _emit("run002_v2_score_reused", arm=arm)
            continue
        generated_sql = {
            example_id: checkpoints[arm][example_id]["generated_sql"]
            for example_id in expected_ids
        }
        evaluation = evaluator.evaluate_batch(
            generated_sql, split="test"
        )
        report = build_final_report(
            config=configs[arm],
            dataset=dataset,
            evaluation_runner=evaluator,
            checkpoint=checkpoints[arm],
            retrieval_index=retrieval if arm == "B4" else None,
            evaluation=evaluation,
        )
        write_json_immutable(report_path, report)
        _emit(
            "run002_v2_score_complete",
            arm=arm,
            correct=evaluation.correct,
            total=evaluation.total,
        )

    reports_dir = output_root / "reports"
    analysis = analyze_reports(
        project_root=PROJECT_ROOT,
        reports_dir=reports_dir,
        plan_path=DEFAULT_PLAN,
    )
    analysis_json = output_root / "analysis/final-analysis.json"
    analysis_csv = output_root / "analysis/final-arm-summary.csv"
    _compare_or_write_analysis(
        analysis,
        json_path=analysis_json,
        csv_path=analysis_csv,
    )

    artifact_paths = [preflight_path, analysis_json, analysis_csv]
    for arm in FINAL_ARMS:
        predictions_path, report_path = _paths(output_root, arm)
        artifact_paths.extend((predictions_path, report_path))
        operational_path = predictions_path.with_name(
            f"{predictions_path.stem}-operational.jsonl"
        )
        if operational_path.exists():
            artifact_paths.append(operational_path)
    completion = {
        "schema_version": 1,
        "run_id": "run002-final-evaluation-v2",
        "status": "complete",
        "freeze_id": freeze["freeze_id"],
        "release_tag": freeze["release_tag"],
        "release_commit": release_commit,
        "source_tree_sha256": freeze["source_tree_sha256"],
        "selected_arms": list(FINAL_ARMS),
        "primary_arm": freeze["primary_arm"],
        "held_out_examples": 104,
        "provider_runs_per_arm": 1,
        "request_delay_seconds": args.request_delay_seconds,
        "transient_retry_seconds": args.transient_retry_seconds,
        "provider_error_policy": "retry_transient_without_prediction",
        "artifacts": [
            artifact_record(path, output_root)
            for path in sorted(artifact_paths)
        ],
    }
    write_json_immutable(output_root / "run-manifest.json", completion)
    _emit(
        "run002_v2_complete",
        primary_arm=freeze["primary_arm"],
        analysis=analysis_json.as_posix(),
    )
    return completion


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the frozen one-time RUN-002 held-out evaluation in protocol order"
        )
    )
    parser.add_argument(
        "--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--request-delay-seconds", type=float, default=10.0
    )
    parser.add_argument(
        "--transient-retry-seconds", type=float, default=60.0
    )
    parser.add_argument("--source-jsonl", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--dataset-config", type=Path, default=DEFAULT_DATASET_CONFIG
    )
    parser.add_argument(
        "--expected-dataset-manifest",
        type=Path,
        default=DEFAULT_DATASET_MANIFEST,
    )
    parser.add_argument(
        "--database-root", type=Path, default=DEFAULT_DATABASE_ROOT
    )
    parser.add_argument(
        "--gold-result-root", type=Path, default=DEFAULT_GOLD_RESULT_ROOT
    )
    parser.add_argument(
        "--standards-jsonl", type=Path, default=DEFAULT_STANDARDS
    )
    parser.add_argument(
        "--retrieval-index", type=Path, default=DEFAULT_RETRIEVAL_INDEX
    )
    parser.add_argument(
        "--retrieval-manifest", type=Path, default=DEFAULT_RETRIEVAL_MANIFEST
    )
    parser.add_argument(
        "--expected-retrieval-manifest",
        type=Path,
        default=DEFAULT_EXPECTED_RETRIEVAL_MANIFEST,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.request_delay_seconds < 0:
            raise FinalRunError(
                "request-delay-seconds must not be negative"
            )
        if args.transient_retry_seconds <= 0:
            raise FinalRunError(
                "transient-retry-seconds must be positive"
            )
        result = execute(args)
    except (
        AnalysisError,
        EvaluationResourceError,
        ExperimentConfigurationError,
        ExperimentRunError,
        FinalRunError,
        FileNotFoundError,
        GroqProviderError,
        RetrievalIndexError,
        OSError,
        ValueError,
    ) as error:
        print(json.dumps({
            "status": "error",
            "error_type": type(error).__name__,
            "message": str(error),
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "status": result["status"],
        "run_id": result["run_id"],
        "output_root": args.output_root.expanduser().resolve().as_posix(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
