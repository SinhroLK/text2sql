from __future__ import annotations

import argparse
import json
from pathlib import Path

from .semantic_errors import (
    SemanticErrorAnalysisError,
    load_semantic_error_spec,
    run_semantic_error_analysis,
    write_semantic_error_artifacts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = (
    PROJECT_ROOT / "configs/analysis/sem001-paired-errors-v1.json"
)
DEFAULT_JSONL = (
    PROJECT_ROOT / "artifacts/reports/sem001-paired-error-corpus.jsonl"
)
DEFAULT_MARKDOWN = (
    PROJECT_ROOT / "artifacts/reports/sem001-paired-error-corpus.md"
)
DEFAULT_MANIFEST = (
    PROJECT_ROOT / "artifacts/reports/sem001-paired-error-manifest.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the provider-free SEM-001 paired semantic-error corpus."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--jsonl-output", type=Path, default=DEFAULT_JSONL)
    parser.add_argument(
        "--markdown-output", type=Path, default=DEFAULT_MARKDOWN
    )
    parser.add_argument(
        "--manifest-output", type=Path, default=DEFAULT_MANIFEST
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        spec = load_semantic_error_spec(args.config)
        analysis = run_semantic_error_analysis(spec, args.project_root)
        manifest = write_semantic_error_artifacts(
            analysis,
            jsonl_path=args.jsonl_output,
            markdown_path=args.markdown_output,
            manifest_path=args.manifest_output,
        )
    except (OSError, SemanticErrorAnalysisError) as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "complete",
                "analysis_id": analysis.analysis_id,
                "summary": analysis.summary,
                "artifacts": manifest["artifacts"],
                "manifest_sha256": manifest["manifest_sha256"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
