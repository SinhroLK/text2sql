from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from text2sql.datasets import load_and_validate_protocol, load_spider2_lite_sqlite
from text2sql.evaluation import OfficialGoldResultStore, Spider2GoldResultRunner, Spider2SQLiteDatabaseResolver
from text2sql.experiments.cli import (PROJECT_ROOT, DEFAULT_SOURCE, DEFAULT_DATASET_CONFIG,
    DEFAULT_DATASET_MANIFEST, DEFAULT_DATABASE_ROOT, DEFAULT_GOLD_RESULT_ROOT, DEFAULT_STANDARDS)

from .config import load_refinement_config
from .provider import B7GroqProvider, INPUT_USD_PER_MILLION, OUTPUT_USD_PER_MILLION
from .runner import generate_run, load_completed_run, prepare_run, score_run
from .quota import TokenQuota


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="REF-001 B7 development preflight and bounded run")
    parser.add_argument("command", choices=("preflight", "run", "score"))
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs/experiments/exp007-b7.toml")
    parser.add_argument("--run-dir", type=Path, default=PROJECT_ROOT / "artifacts/experiments/exp007-b7")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--auto-quota", action="store_true")
    parser.add_argument("--tokens-per-minute", type=int, default=8000)
    parser.add_argument("--tokens-per-day", type=int, default=200000)
    parser.add_argument("--safety-margin", type=float, default=0.9)
    args = parser.parse_args(argv)
    if args.command == "run" and args.max_cost_usd is None:
        parser.error("run requires --max-cost-usd")
    try:
        config = load_refinement_config(args.config)
        dataset = load_spider2_lite_sqlite(DEFAULT_SOURCE, DEFAULT_DATASET_CONFIG, PROJECT_ROOT, DEFAULT_DATASET_MANIFEST)
        protocol, _ = load_and_validate_protocol(DEFAULT_DATASET_CONFIG, PROJECT_ROOT)
        ids = {e.example_id for e in dataset.for_split("development")}
        if len(ids) != 31:
            raise ValueError("Frozen B7 CLI requires exactly 31 development IDs")
        evaluator = Spider2GoldResultRunner(dataset=dataset,
            database_resolver=Spider2SQLiteDatabaseResolver(DEFAULT_DATABASE_ROOT),
            gold_results=OfficialGoldResultStore.from_official_directory(DEFAULT_GOLD_RESULT_ROOT, DEFAULT_STANDARDS,
                expected_metadata_sha256=protocol["source"]["evaluation_manifest_sha256"], allowed_example_ids=ids))
        identity, prepared = prepare_run(config, dataset, evaluator)
        payload = {"status": "ready", "identity": identity, "provider_calls": 0,
                   "maximum_provider_calls": config.max_calls,
                   "maximum_reserved_cost_usd": config.max_calls * (
                       config.max_input_tokens * INPUT_USD_PER_MILLION + config.base.max_tokens * OUTPUT_USD_PER_MILLION) / 1_000_000}
        if args.command == "run":
            key = os.environ.get("GROQ_API_KEY")
            if not key:
                raise ValueError("GROQ_API_KEY is required for the paid B7 run")
            quota = TokenQuota(args.run_dir / "quota.jsonl",
                tokens_per_minute=args.tokens_per_minute, tokens_per_day=args.tokens_per_day,
                safety_margin=args.safety_margin) if args.auto_quota else None
            provider = B7GroqProvider(config, api_key=key, quota=quota)
            run = generate_run(config, prepared, identity, provider, args.run_dir, args.max_cost_usd)
            payload = score_run(run, evaluator)
        elif args.command == "score":
            payload = score_run(load_completed_run(config, identity, args.run_dir), evaluator)
        output = args.output or (args.run_dir / "report.json" if args.command != "preflight" else None)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        # Infrastructure/resource errors are reported without raw provider text.
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
