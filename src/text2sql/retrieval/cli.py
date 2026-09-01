from __future__ import annotations

import argparse
import json
from pathlib import Path

from .index import (
    build_spider1_train_retrieval_index,
    build_spider2_leakage_firewall,
    load_retrieval_config,
    sha256_file,
    write_retrieval_index,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the checksum-gated Spider 1.0 train-only retrieval index."
    )
    parser.add_argument(
        "--train-json",
        type=Path,
        default=PROJECT_ROOT / "data/raw/spider1/spider_data/train_spider.json",
    )
    parser.add_argument(
        "--tables-json",
        type=Path,
        default=PROJECT_ROOT / "data/raw/spider1/spider_data/tables.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs/datasets/spider1-train-retrieval-v1.toml",
    )
    parser.add_argument(
        "--spider2-metadata",
        type=Path,
        default=PROJECT_ROOT
        / "data/processed/spider2-lite-sqlite-v1/examples.jsonl",
    )
    parser.add_argument(
        "--spider2-split-manifest",
        type=Path,
        default=PROJECT_ROOT
        / "configs/datasets/spider2-lite-sqlite-split-v1.json",
    )
    parser.add_argument(
        "--expected-manifest",
        type=Path,
        default=PROJECT_ROOT
        / "configs/datasets/spider1-train-retrieval-manifest-v1.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "artifacts/retrieval/spider1-train-v1",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_retrieval_config(args.config)
    firewall = build_spider2_leakage_firewall(
        args.spider2_metadata,
        args.spider2_split_manifest,
        expected_metadata_sha256=config.spider2_metadata_sha256,
        expected_split_manifest_sha256=config.spider2_split_manifest_sha256,
    )
    index = build_spider1_train_retrieval_index(
        args.train_json,
        args.tables_json,
        args.config,
        firewall,
        expected_manifest_path=args.expected_manifest,
    )
    index_path, manifest_path = write_retrieval_index(
        index, args.output_dir, overwrite=args.overwrite
    )
    print(
        json.dumps(
            {
                "index_id": index.manifest["index_id"],
                "entries": len(index.entries),
                "databases": index.manifest["counts"]["databases"],
                "index_path": str(index_path),
                "index_sha256": sha256_file(index_path),
                "manifest_path": str(manifest_path),
                "manifest_sha256": sha256_file(manifest_path),
                "leakage_audit": index.manifest["leakage_audit"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
