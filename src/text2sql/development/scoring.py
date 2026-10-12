"""Separate bounded scoring after every development generation is terminal."""
from __future__ import annotations

import ast
from collections import defaultdict
from contextlib import redirect_stdout
from decimal import Decimal
import io
import json
import math
from pathlib import Path

import pandas as pd

from text2sql.evaluation import OfficialGoldResultStore
from text2sql.execution import SandboxPolicy, SQLiteSandboxExecutor

from .controls import Limits, RequestJournal, digest
from .run import sha, validate_generation, write_json


def official_comparator(path: Path, expected_hash: str):
    if sha(path) != expected_hash:
        raise ValueError("Official comparator checksum mismatch")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "compare_pandas_table"]
    if len(functions) != 1:
        raise ValueError("Pinned comparator is missing")
    # Execute only the exact pinned comparison function, not upstream cloud SDK imports.
    scope = {"pd": pd, "math": math}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), scope)
    return scope["compare_pandas_table"]


def score_record(record: dict, database: Path, gold, executor, comparator) -> dict:
    result = {"key": record["key"], "arm": record["arm"], "example_id": record["example_id"],
              "db_id": record["db_id"], "correct": False, "nonempty_correct": False,
              "status": "generation_failure", "generation_status": record["status"]}
    if record["status"] != "complete" or not record["generated_sql"]:
        return result
    execution = executor.validate_and_execute(database, record["generated_sql"])
    result["execution"] = execution.to_dict()
    if not execution.succeeded or execution.truncated:
        result["status"] = execution.status if execution.status != "success" else "incomplete_result"
        return result
    predicted = pd.DataFrame(list(execution.rows), columns=execution.columns)
    issues = []
    for variant in gold.variants:
        try:
            reference = pd.read_csv(variant.path)
            with redirect_stdout(io.StringIO()):
                matches = comparator(predicted, reference, list(variant.condition_cols or []), gold.ignore_order)
            if matches:
                result.update(correct=True, nonempty_correct=bool(execution.rows), status="correct",
                              matched_gold_file=variant.path.name)
                return result
        except (ValueError, TypeError, IndexError) as error:
            issues.append(type(error).__name__)
    result["status"] = "comparison_error" if issues else "result_mismatch"
    if issues:
        result["comparison_issues"] = issues
    return result


def summarize(records: list[dict], identity: dict, reservations: list[dict]) -> dict:
    arms = [a["arm"] for a in identity["config"]["arms"]]
    families = {db: f["family_id"] for f in identity["target_families"] for db in f["databases"]}
    scores = {}
    by_arm = {}
    for arm in arms:
        rows = [r for r in records if r["arm"] == arm]
        by_arm[arm] = {r["example_id"]: r for r in rows}
        by_database = defaultdict(lambda: {"correct": 0, "total": 0})
        for r in rows:
            by_database[r["db_id"]]["total"] += 1
            by_database[r["db_id"]]["correct"] += int(r["correct"])
        costs = sum((Decimal(r["reserved_usd"]) for r in reservations
                     if r["example_id"].startswith(arm + ":")), Decimal(0))
        correct = sum(r["correct"] for r in rows)
        scores[arm] = {"correct": correct, "total": len(rows),
                       "execution_accuracy": correct / len(rows),
                       "nonempty_correct": sum(r["nonempty_correct"] for r in rows),
                       "generation_failures": sum(r["status"] == "generation_failure" for r in rows),
                       "executable": sum(r.get("execution", {}).get("status") == "success"
                                         and not r.get("execution", {}).get("truncated") for r in rows),
                       "reserved_usd_at_artificial_ceiling": str(costs), "by_database": dict(by_database)}
    comparisons = []
    eligible = ["C0"]
    for baseline, candidate in (("C0", "C1"), ("C1", "C2"), ("C0", "C2")):
        gains = [i for i, r in by_arm[candidate].items() if r["correct"] and not by_arm[baseline][i]["correct"]]
        losses = [i for i, r in by_arm[baseline].items() if r["correct"] and not by_arm[candidate][i]["correct"]]
        net_by_family = defaultdict(int)
        for i in gains:
            net_by_family[families[by_arm[candidate][i]["db_id"]]] += 1
        for i in losses:
            net_by_family[families[by_arm[baseline][i]["db_id"]]] -= 1
        improved_families = {family for family, gain in net_by_family.items() if gain > 0}
        qualifies = len(gains) - len(losses) >= 3 and len(losses) <= 2 and len(improved_families) >= 2
        comparisons.append({"baseline": baseline, "candidate": candidate, "gained_ids": gains,
                            "lost_ids": losses, "improved_families": sorted(improved_families),
                            "passes_promotion_rule": qualifies})
        if baseline == "C0" and qualifies:
            eligible.append(candidate)
    settings = {a["arm"]: a for a in identity["config"]["arms"]}
    winner = min(eligible, key=lambda a: (-scores[a]["correct"], Decimal(scores[a]["reserved_usd_at_artificial_ceiling"]),
                                        settings[a]["max_completion_tokens"],
                                        {"low": 0, "medium": 1, "high": 2}[settings[a]["reasoning_effort"]]))
    return {"arms": scores, "paired_comparisons": comparisons, "selected_arm": winner,
            "selection_scope": "reused development only; no untouched-test accuracy claim",
            "limitation": "All arms share the frozen bounded executor. These execution limits differ from historical B1/B6R reports."}


def score_run(root: Path, directory: Path, manifest: dict) -> int:
    identity = manifest["identity"]
    if identity["mock"]:
        raise ValueError("Mock rehearsals are not model experiments and cannot produce benchmark scores")
    if not (directory / "generation.json").exists():
        raise ValueError("Finish all three arms before scoring; resume generation first")
    with RequestJournal(directory / "requests.jsonl", {"expected_ids": identity["expected_ids"],
                        "run_sha256": manifest["identity_sha256"]}, Limits(**identity["limits"])) as journal:
        generation = validate_generation(directory, manifest, journal)
        generation_sha = sha(directory / "generation.json")
        if (directory / "scores.json").exists():
            previous = json.loads((directory / "scores.json").read_text())
            if previous["generation_sha256"] != generation_sha:
                raise ValueError("Score file belongs to different predictions")
            print(json.dumps(previous["summary"], sort_keys=True))
            return 0
        upstream = root / "data/raw/spider2/spider2-lite/evaluation_suite"
        metadata = upstream / "gold/spider2lite_eval.jsonl"
        gold = OfficialGoldResultStore.from_official_directory(upstream / "gold/exec_result", metadata,
                    expected_metadata_sha256=identity["resources"][metadata.relative_to(root).as_posix()],
                    allowed_example_ids=[p["example_id"] for p in identity["prompts"]])
        for prompt in identity["prompts"]:
            actual = [v.path.relative_to(root).as_posix() for v in gold.get(prompt["example_id"]).variants]
            if actual != identity["gold_files"][prompt["example_id"]]:
                raise ValueError("Gold variant inventory changed after preparation")
        utils = upstream / "evaluate_utils.py"
        comparator = official_comparator(utils, identity["resources"][utils.relative_to(root).as_posix()])
        executor = SQLiteSandboxExecutor(SandboxPolicy(**identity["config"]["execution_limits"]))
        database_by_id = {p["example_id"]: root / p["database_path"] for p in identity["prompts"]}
        checkpoint_path = directory / "score-checkpoint.json"
        saved = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {
            "generation_sha256": generation_sha, "records": []}
        if saved["generation_sha256"] != generation_sha:
            raise ValueError("Scoring checkpoint refers to changed predictions")
        if [r["key"] for r in saved["records"]] != identity["expected_ids"][:len(saved["records"])]:
            raise ValueError("Scoring checkpoint has invalid coverage")
        if saved.get("records_sha256", digest([])) != digest(saved["records"]):
            raise ValueError("Scoring checkpoint checksum mismatch")
        for record in generation["records"][len(saved["records"]):]:
            result = score_record(record, database_by_id[record["example_id"]], gold.get(record["example_id"]),
                                  executor, comparator)
            saved["records"].append(result)
            saved["records_sha256"] = digest(saved["records"])
            write_json(checkpoint_path, saved)
            print(json.dumps({"event": "score", "key": record["key"], "status": result["status"]}), flush=True)
        summary = summarize(saved["records"], identity, [e for e in journal.events if e["event"] == "reserve"])
        write_json(directory / "scores.json", {"run_sha256": manifest["identity_sha256"],
                   "generation_sha256": generation_sha, "comparator_sha256": sha(utils),
                   "execution_limits": identity["config"]["execution_limits"], "summary": summary,
                   "records": saved["records"]})
        lines = ["# Recovery development results", "", "All 31 examples count in every arm, including failures.", "",
                 "| Arm | Correct | Executable | Generation failures |", "|---|---:|---:|---:|"]
        for arm, item in summary["arms"].items():
            lines.append(f"| {arm} | {item['correct']}/{item['total']} | {item['executable']} | {item['generation_failures']} |")
        lines.extend(["", "Selected by the predeclared development rule: **" + summary["selected_arm"] + "**.",
                      "", summary["limitation"], "", "This is development evidence, not a fresh final evaluation."])
        (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0
