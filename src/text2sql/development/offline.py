"""Build the recovery handoff entirely offline; there is no live-provider mode."""
from __future__ import annotations

import argparse
import hashlib
from decimal import Decimal
from importlib.metadata import distributions
import json
from pathlib import Path

from .controls import Limits, digest
from .diagnostics import build
from .preflight import prepare
from .tokenization import VOCABULARY_SHA256, load_local_counter, reserve_input_tokens


def budget_preflight(prompts: list[dict], plan: dict, *, token_counter=None) -> dict:
    if plan.get("paid_runs_enabled") is not False or plan.get("provider_max_retries") != 0:
        raise ValueError("Offline preparation requires disabled paid calls and hidden retries")
    quota, rates = plan["quota_scenario"], plan["planning_rate_ceilings"]
    limits = Limits(**{k: plan[k] for k in ("max_calls", "max_total_tokens",
                                          "max_attempts_per_example", "max_cost_usd")},
                    **{k: quota[k] for k in ("tokens_per_minute", "tokens_per_day")},
                    **{k: rates[k] for k in ("input_usd_per_million", "output_usd_per_million")})
    rows = []
    for arm in plan["arms"]:
        completion = arm["max_completion_tokens"]
        if type(completion) is not int or completion <= 0:
            raise ValueError("Completion reservation must be a positive integer")
        for prompt in prompts:
            byte_bound = len(prompt["prompt"].encode("utf-8")) + 256
            text_tokens = token_counter(prompt["prompt"]) if token_counter else None
            inputs = reserve_input_tokens(text_tokens) if text_tokens is not None else byte_bound
            tokens = inputs + completion
            rows.append({"arm": arm["arm"], "example_id": prompt["example_id"],
                         "input_token_reservation": inputs, "completion_tokens": completion,
                         "input_reservation_method": "local_o200k_plus_allowance" if token_counter else "utf8_bytes_plus_allowance",
                         "ordinary_text_tokens": text_tokens,
                         "utf8_byte_bound_plus_allowance": byte_bound,
                         "reserved_tokens": tokens,
                         "reserved_usd_at_planning_ceiling": str(limits.cost(inputs, completion)),
                         "fits_recorded_quota_scenario": tokens <= min(limits.tokens_per_minute,
                                                                        limits.tokens_per_day)})
    multiplier = limits.max_attempts_per_example
    total_cost = sum((Decimal(row["reserved_usd_at_planning_ceiling"]) for row in rows), Decimal(0)) * multiplier
    tokens = sum(row["reserved_tokens"] for row in rows) * multiplier
    return {"provider_calls": 0, "paid_run_ready": False,
            "plan_sha256": digest(plan), "request_count": len(rows),
            "maximum_attempts": len(rows) * multiplier, "maximum_reserved_tokens": tokens,
            "maximum_reserved_usd_at_planning_ceiling": str(total_cost),
            "fits_total_caps": (len(rows) * multiplier <= limits.max_calls
                                and tokens <= limits.max_total_tokens
                                and total_cost <= Decimal(limits.max_cost_usd)),
            "requests_exceeding_recorded_quota_scenario": sum(not row["fits_recorded_quota_scenario"] for row in rows),
            "tokenizer_vocabulary_sha256": VOCABULARY_SHA256 if token_counter else None,
            "limitation": "Local ordinary-text tokens plus 256 chat-template allowance and 15% margin, or UTF-8 fallback. The provider template and current account quota/pricing still require confirmation. Never silently drop context.",
            "requests": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("Choose a fresh output directory")
    root = args.project_root.resolve()
    result = prepare(root, root / "configs/development/recovery-development-v2.json",
                     root / "configs/development/task-documents-v1.json")
    prompts = result.pop("prompts")
    counter = load_local_counter(root / "data/raw/tokenizers/o200k_base.tiktoken")
    budget = budget_preflight(prompts, json.loads(
        (root / "configs/development/baseline-ablation-v1.json").read_text(encoding="utf-8")), token_counter=counter)
    corpus = build(root)
    environment = {dist.metadata["Name"]: dist.version for dist in distributions()
                   if dist.metadata.get("Name")}
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, rows in (("prompts", prompts), ("analysis-development", corpus.pop("development")),
                       ("analysis-reviewed-test", corpus.pop("reviewed_test_diagnostics"))):
        (args.output_dir / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    for name, value in (("preflight", result), ("budget", budget), ("analysis-summary", corpus),
                        ("installed-packages", environment)):
        (args.output_dir / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(args.output_dir.iterdir())}
    (args.output_dir / "artifact-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"provider_calls": 0, "development_prompts": len(prompts),
                      "retrieval_exclusions": result["filtered_retrieval_manifest"]["excluded_examples"],
                      "paid_run_ready": False,
                      "quota_scenario_oversized_requests": budget["requests_exceeding_recorded_quota_scenario"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
