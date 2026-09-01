from __future__ import annotations

import argparse
import json
from pathlib import Path

from text2sql.datasets import load_spider2_lite_sqlite
from text2sql.retrieval import load_verified_retrieval_index

from .config import ExperimentConfigurationError, load_baseline_config
from .retrieval_audit import audit_development_retrieval


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = (
    PROJECT_ROOT / "data/raw/spider2/spider2-lite/spider2-lite.jsonl"
)
DEFAULT_DATASET_CONFIG = (
    PROJECT_ROOT / "configs/datasets/spider2-lite-sqlite-v1.toml"
)
DEFAULT_DATASET_MANIFEST = (
    PROJECT_ROOT
    / "configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit B3/B4 retrieval without contacting Groq."
    )
    parser.add_argument("--experiment-config", type=Path, required=True)
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
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_baseline_config(args.experiment_config)
        if config.baseline not in {"B3", "B4"}:
            raise ExperimentConfigurationError(
                "Retrieval audit requires a B3 or B4 configuration"
            )
        dataset = load_spider2_lite_sqlite(
            args.source_jsonl,
            args.dataset_config,
            PROJECT_ROOT,
            args.expected_dataset_manifest,
        )
        retrieval_index = load_verified_retrieval_index(
            args.retrieval_index,
            args.retrieval_manifest,
            expected_manifest_path=args.expected_retrieval_manifest,
        )
        payload = audit_development_retrieval(
            config=config,
            dataset=dataset,
            retrieval_index=retrieval_index,
        )
        output = (
            args.output
            or PROJECT_ROOT
            / "artifacts/reports"
            / f"{config.experiment_id}-retrieval-audit.json"
        ).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        rendered = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        temporary = output.with_name(f".{output.name}.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(output)
    except (
        ExperimentConfigurationError,
        FileNotFoundError,
        ValueError,
    ) as error:
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
                "output": str(output),
                "summary": payload["summary"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
