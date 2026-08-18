from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from text2sql.datasets import load_spider2_lite_sqlite
from text2sql.datasets.protocol import load_and_validate_protocol

from .evaluator import SQLiteExecutionEvaluator
from .resources import (
    EvaluationResourceError,
    ProtectedReferenceSQLStore,
    Spider2SQLiteDatabaseResolver,
)
from .spider2_runner import Spider2EvaluationRunner, load_generated_sql_jsonl


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROTOCOL = PROJECT_ROOT / "configs/datasets/spider2-lite-sqlite-v1.toml"
DEFAULT_DATASET_MANIFEST = PROJECT_ROOT / "configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json"
DEFAULT_SOURCE = PROJECT_ROOT / "data/raw/spider2/spider2-lite/spider2-lite.jsonl"
DEFAULT_DATABASE_ROOT = PROJECT_ROOT / "data/raw/spider2/spider2-lite/resource/databases/spider2-localdb"
DEFAULT_REFERENCE_ROOT = PROJECT_ROOT / "data/private/spider2-lite/gold/sql"
DEFAULT_STANDARDS = PROJECT_ROOT / "data/raw/spider2/spider2-lite/evaluation_suite/gold/spider2lite_eval.jsonl"


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-jsonl", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--config", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--expected-dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST)
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--reference-root", type=Path, default=DEFAULT_REFERENCE_ROOT)
    parser.add_argument("--standards-jsonl", type=Path, default=DEFAULT_STANDARDS)
    parser.add_argument("--timeout", type=float, default=60.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pinned Spider2-Lite SQLite integration runner (EVAL-002).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Validate evaluation resources.")
    _add_common_arguments(preflight)
    preflight.add_argument("--split", choices=("development", "test"), default="development")
    preflight.add_argument("--manifest-output", type=Path, default=None)

    single = subparsers.add_parser("single", help="Evaluate one frozen Spider2-Lite example.")
    _add_common_arguments(single)
    single.add_argument("--example-id", required=True)
    generated = single.add_mutually_exclusive_group(required=True)
    generated.add_argument("--generated-sql")
    generated.add_argument("--generated-sql-file", type=Path)

    batch = subparsers.add_parser("batch", help="Evaluate one exact frozen split from JSONL.")
    _add_common_arguments(batch)
    batch.add_argument("--split", choices=("development", "test"), default="development")
    batch.add_argument("--predictions", type=Path, required=True)
    batch.add_argument("--output", type=Path, default=None)
    return parser


def _build_runner(args: argparse.Namespace) -> Spider2EvaluationRunner:
    protocol, _ = load_and_validate_protocol(args.config, PROJECT_ROOT)
    dataset = load_spider2_lite_sqlite(
        args.source_jsonl,
        args.config,
        PROJECT_ROOT,
        args.expected_dataset_manifest,
    )
    return Spider2EvaluationRunner(
        dataset=dataset,
        database_resolver=Spider2SQLiteDatabaseResolver(args.database_root),
        references=ProtectedReferenceSQLStore.from_official_directory(
            args.reference_root,
            args.standards_jsonl,
            expected_metadata_sha256=protocol["source"]["evaluation_manifest_sha256"],
        ),
        evaluator=SQLiteExecutionEvaluator(timeout_seconds=args.timeout),
    )


def _write_json(path: Path | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if path is not None:
        resolved = path.expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(rendered, encoding="utf-8")


def _error_payload(error: Exception) -> dict[str, Any]:
    if isinstance(error, EvaluationResourceError):
        return {"status": "resource_error", "error": error.to_dict()}
    return {"status": "error", "error_type": type(error).__name__, "message": str(error)}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        runner = _build_runner(args)
        if args.command == "preflight":
            report = runner.preflight(split=args.split)
            payload: dict[str, Any] = {
                "status": "ready" if report.ready else "blocked",
                "preflight": report.to_dict(),
            }
            if report.ready and args.manifest_output is not None:
                manifest = runner.resource_manifest(split=args.split)
                _write_json(args.manifest_output, manifest)
            else:
                _write_json(None, payload)
            return 0 if report.ready else 2

        if args.command == "single":
            generated_sql = (
                args.generated_sql
                if args.generated_sql is not None
                else args.generated_sql_file.read_text(encoding="utf-8")
            )
            result = runner.evaluate_one(args.example_id, generated_sql)
            _write_json(None, result.to_dict())
            status = result.result.status
            if status.endswith("execution_error") or status == "comparison_error":
                return 2
            return 0 if result.result.correct else 1

        predictions = load_generated_sql_jsonl(args.predictions)
        report = runner.evaluate_batch(predictions, split=args.split)
        _write_json(args.output, report.to_dict())
        return 0
    except (EvaluationResourceError, FileNotFoundError, ValueError) as error:
        _write_json(None, _error_payload(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
