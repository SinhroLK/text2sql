from __future__ import annotations

import argparse
import json
from pathlib import Path

from .spider2_lite import load_spider2_lite_sqlite, sha256_file, write_processed_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and normalize the frozen Spider2-Lite SQLite metadata scope."
    )
    parser.add_argument(
        "--source-jsonl",
        type=Path,
        default=PROJECT_ROOT / "data/raw/spider2/spider2-lite/spider2-lite.jsonl",
        help="Path to spider2-lite.jsonl from the pinned upstream commit.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs/datasets/spider2-lite-sqlite-v1.toml",
        help="Frozen DATA-001 TOML protocol.",
    )
    parser.add_argument(
        "--expected-manifest",
        type=Path,
        default=PROJECT_ROOT / "configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json",
        help="Version-controlled DATA-003 metadata manifest.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data/processed/spider2-lite-sqlite-v1",
        help="Directory for normalized metadata artifacts.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing artifacts only when their content differs.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    dataset = load_spider2_lite_sqlite(
        source_jsonl=args.source_jsonl,
        config_path=args.config,
        project_root=PROJECT_ROOT,
        expected_manifest_path=args.expected_manifest,
    )
    examples_path, manifest_path = write_processed_dataset(
        dataset,
        args.output_dir,
        overwrite=args.overwrite,
    )
    summary = {
        "dataset_id": dataset.manifest["dataset_id"],
        "examples": len(dataset.examples),
        "development": len(dataset.for_split("development")),
        "test": len(dataset.for_split("test")),
        "examples_path": str(examples_path),
        "examples_sha256": sha256_file(examples_path),
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
