from __future__ import annotations

import argparse
import json
from pathlib import Path

from .index import load_verified_retrieval_index, sha256_file
from .structural import (
    build_structural_index,
    load_structural_retrieval_config,
    write_structural_index,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the deterministic Spider 1.0 train SQL-structure index."
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
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs/retrieval/ret003-structural-v1.toml",
    )
    parser.add_argument(
        "--expected-manifest",
        type=Path,
        default=PROJECT_ROOT
        / "configs/retrieval/ret003-structural-manifest-v1.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT
        / "artifacts/retrieval/spider1-train-structural-v1",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--skip-expected-manifest",
        action="store_true",
        help="Allow bootstrapping a new expected manifest; never needed for normal builds.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_structural_retrieval_config(args.config)
    source = load_verified_retrieval_index(
        args.source_index,
        args.source_manifest,
        expected_manifest_path=args.expected_source_manifest,
    )
    index = build_structural_index(config, source)
    if not args.skip_expected_manifest:
        expected = json.loads(args.expected_manifest.read_text(encoding="utf-8"))
        if index.manifest != expected:
            raise ValueError(
                "generated structural manifest does not match the version-controlled contract"
            )
    index_path, manifest_path = write_structural_index(
        index, args.output_dir, overwrite=args.overwrite
    )
    print(
        json.dumps(
            {
                "index_id": index.manifest["index_id"],
                "entries": len(index.entries),
                "index_path": str(index_path),
                "index_sha256": sha256_file(index_path),
                "manifest_path": str(manifest_path),
                "manifest_sha256": sha256_file(manifest_path),
                "leakage_audit": index.manifest["leakage_audit"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
