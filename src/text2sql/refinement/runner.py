from __future__ import annotations

import importlib.metadata
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.evaluation import Spider2GoldResultRunner
from text2sql.evaluation.resources import sha256_path
from text2sql.execution import SQLiteSandboxExecutor
from text2sql.providers.base import SQLProvider

from .config import RefinementConfig
from .core import STRATEGIES, RefinementPipeline, digest, prepare_context
from .journal import CallJournal
from .provider import count_input_tokens


def prepare_run(config: RefinementConfig, dataset: LoadedSpider2LiteDataset,
                evaluator: Spider2GoldResultRunner) -> tuple[dict[str, Any], dict[str, Any]]:
    examples = sorted(dataset.for_split("development"), key=lambda e: e.example_id)
    if not examples or len({e.example_id for e in examples}) != len(examples):
        raise ValueError("Invalid B7 development coverage")
    resources = evaluator.resource_manifest(split="development")
    prepared, databases, token_estimates = {}, {}, {}
    sandbox = SQLiteSandboxExecutor(config.sandbox)
    for example in examples:
        database = evaluator.database_resolver.resolve(example.db_id)
        if example.db_id not in databases:
            probe = sandbox.validate_and_execute(database.path, "SELECT 1")
            if not probe.succeeded or probe.database_sha256 != database.sha256:
                raise ValueError(f"B7 sandbox preflight failed for {example.db_id}: {probe.error_code}")
            databases[example.db_id] = {"sha256": database.sha256, "bytes": database.path.stat().st_size}
        schema, context = prepare_context(example.question, database.path, example.db_id, config)
        estimates = [count_input_tokens(strategy + context, config.base.model_id)
                     for strategy in STRATEGIES[:config.max_candidates]]
        if max(estimates) > config.max_input_tokens:
            raise ValueError(f"B7 input token budget exceeded for {example.example_id}")
        prepared[example.example_id] = (example, database.path, schema, context)
        token_estimates[example.example_id] = estimates
    src = Path(__file__).resolve().parents[1]
    source_identity = {str(path.relative_to(src)): digest(path.read_text()) for path in sorted(src.rglob("*.py"))}
    identity = {
        "experiment_id": config.experiment_id, "config_sha256": config.config_sha256,
        "base_config_sha256": config.base.config_sha256,
        "expected_ids": [e.example_id for e in examples], "split": "development",
        "dataset_sha256": digest(dataset.manifest), "resources": resources,
        "contexts": {key: digest([item[0].question, item[3]]) for key, item in prepared.items()},
        "source_sha256": digest(source_identity), "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
        "dependencies": {name: importlib.metadata.version(name) for name in ("groq", "sqlparse", "litellm", "tiktoken")},
        "sandbox_policy": asdict(config.sandbox), "databases": databases,
        "initial_input_token_estimates": token_estimates,
        "provider": "groq", "model_id": config.base.model_id,
    }
    return identity, prepared


def read_predictions(path: Path, identity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return records
    for line in path.read_text().splitlines():
        data = json.loads(line)
        record = data["record"]
        key = record["example_id"]
        if (data["identity_sha256"] != digest(identity) or data["sha256"] != digest(record)
                or key not in identity["expected_ids"] or key in records):
            raise ValueError("B7 prediction identity, integrity or coverage mismatch")
        records[key] = record
    return records


def generate_run(config: RefinementConfig, prepared: dict[str, Any], identity: dict[str, Any],
                 provider: SQLProvider, directory: Path, max_cost_usd: float) -> dict[str, Any]:
    if provider.model_id != config.base.model_id:
        raise ValueError("B7 provider does not match the frozen model")
    journal = CallJournal(directory / "calls.jsonl", identity, config, provider, max_cost_usd)
    try:
        path = directory / "predictions.jsonl"
        records = read_predictions(path, journal.identity)
        pipeline = RefinementPipeline(config, provider)
        for key, (example, database, schema, context) in prepared.items():
            if key in records:
                continue
            if "databases" in identity and sha256_path(database) != identity["databases"][example.db_id]["sha256"]:
                raise ValueError("B7 database changed after preflight")
            outcome = pipeline.run(example.question, database, schema, context,
                                   call=lambda step, request, key=key: journal.call(key + ":" + step, request))
            if "databases" in identity:
                expected_hash = identity["databases"][example.db_id]["sha256"]
                if any(c.get("sandbox", {}).get("database_sha256") not in {None, expected_hash} for c in outcome["candidates"]):
                    raise ValueError("B7 executed a changed database snapshot")
            record = {"example_id": key, "db_id": example.db_id, **outcome}
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"identity_sha256": digest(journal.identity), "sha256": digest(record), "record": record}, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            records[key] = record
            print(json.dumps({"event": "b7_example_completed", "example_id": key,
                              "completed": len(records), "total": len(prepared),
                              "status": outcome["status"], "reserved_usd": journal.reserved_cost}), flush=True)
        if set(records) != set(identity["expected_ids"]):
            raise ValueError("B7 predictions do not cover the exact development split")
        return {"identity": journal.identity, "records": records,
                "provider_calls": len(journal.reservations), "replayed_calls": journal.replayed,
                "reserved_cost_usd": journal.reserved_cost,
                "journal_sha256": journal.previous}
    finally:
        journal.close()


def load_completed_run(config: RefinementConfig, identity: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Verify a completed run for provider-free scoring, including its call ledger."""
    class NoProvider:
        model_id = config.base.model_id
        provider_name = "offline"

        def generate(self, request):
            raise RuntimeError("Offline scoring cannot call a provider")

    journal_path = directory / "calls.jsonl"
    with journal_path.open() as stream:
        cap = json.loads(stream.readline())["data"]["identity"]["max_cost_usd"]
    journal = CallJournal(journal_path, identity, config, NoProvider(), cap)
    try:
        records = read_predictions(directory / "predictions.jsonl", journal.identity)
        if set(records) != set(identity["expected_ids"]):
            raise ValueError("B7 scoring requires complete development predictions")
        return {"identity": journal.identity, "records": records,
                "provider_calls": len(journal.reservations), "replayed_calls": 0,
                "reserved_cost_usd": journal.reserved_cost, "journal_sha256": journal.previous}
    finally:
        journal.close()


def score_run(run: dict[str, Any], evaluator: Spider2GoldResultRunner) -> dict[str, Any]:
    # This function runs only after all selection/repair decisions are durable.
    # No reference result is passed back to the generation/ranking modules.
    expected = run["identity"]["expected_ids"]
    records = run["records"]
    if set(records) != set(expected):
        raise ValueError("Scoring requires exact development coverage")
    if "resources" in run["identity"] and evaluator.resource_manifest(split="development") != run["identity"]["resources"]:
        raise ValueError("B7 evaluation resources changed after generation")
    details = []
    for key in expected:
        record = records[key]
        scores, cache = {}, {}
        for c in record["candidates"]:
            if c["status"] != "success":
                scores[c["candidate_id"]] = {"correct": False, "status": c["status"]}
                continue
            if c["sql"] not in cache:
                result = evaluator.evaluate_one(key, c["sql"]).result
                cache[c["sql"]] = {"correct": result.correct, "status": result.status}
            scores[c["candidate_id"]] = cache[c["sql"]]
        correct = lambda candidate_id: bool(scores.get(candidate_id, {}).get("correct", False))
        repair = next((c for c in record["candidates"] if c["kind"] == "repair"), None)
        details.append({
            "example_id": key, "db_id": record["db_id"],
            "first_pass_correct": correct(0), "selection_only_correct": correct(record["selection_only_id"]),
            "final_correct": correct(record["selected_candidate_id"]),
            "repair_attempted": repair is not None,
            "repair_correct": correct(repair["candidate_id"]) if repair else False,
            "oracle_initial_correct": any(correct(c["candidate_id"]) for c in record["candidates"] if c["kind"] == "initial"),
            "candidate_scores": scores,
        })
    total = len(expected)
    attempts = sum(d["repair_attempted"] for d in details)
    successes = sum(d["repair_correct"] for d in details)
    return {
        "schema_version": 1, "experiment": run["identity"], "expected_ids": expected,
        "evaluation": {name: {"correct": sum(d[field] for d in details), "total": total,
                                "execution_accuracy": sum(d[field] for d in details) / total}
                       for name, field in (("first_pass", "first_pass_correct"), ("selection_only", "selection_only_correct"),
                                           ("final", "final_correct"), ("oracle_initial_at_3", "oracle_initial_correct"))},
        "repair": {"attempts": attempts, "correct": successes, "success_rate": successes / attempts if attempts else None},
        "generation": {"input_tokens": sum(r["input_tokens"] for r in records.values()),
                       "output_tokens": sum(r["output_tokens"] for r in records.values()),
                       "duration_ms": sum(r["duration_ms"] for r in records.values()),
                       "provider_calls": run["provider_calls"], "replayed_calls": run["replayed_calls"],
                       "reserved_cost_usd": run["reserved_cost_usd"]},
        "per_database": {db: {"total": sum(d["db_id"] == db for d in details),
                              "final_correct": sum(d["final_correct"] for d in details if d["db_id"] == db)}
                         for db in sorted({d["db_id"] for d in details})},
        "predictions_sha256": digest(records), "journal_sha256": run["journal_sha256"],
        "records": details,
    }
