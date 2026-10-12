"""Prepare a grounded-prompt experiment, preserving an existing frozen baseline.

Preparation is offline. Generation/scoring use the existing durable run CLI.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from text2sql.datasets.documents import TaskDocumentStore
from text2sql.prompting.grounded import GroundedSQLPipeline, GroundingPolicy, PROMPT_VERSION
from text2sql.providers import MockSchemaAwareProvider
from .controls import digest
from .offline import budget_preflight
from .run import PROJECT_ROOT, load_run, sha, write_json
from .tokenization import load_local_counter, reserve_input_tokens


def prepare_grounded_run(root: Path, source: Path, directory: Path, *, mock=False) -> dict:
    root, source, directory = root.resolve(), source.resolve(), directory.resolve()
    if directory.exists():
        raise ValueError("Output directory exists; use a fresh directory or resume it")
    original = load_run(root, source)
    identity = deepcopy(original["identity"])
    if identity["config"]["prompt_variant"] != "simple_schema":
        raise ValueError("Prompt comparison requires the original simple-schema baseline")
    store = TaskDocumentStore.from_manifest(
        root / "data/raw/spider2/spider2-lite/resource/documents",
        root / "configs/development/task-documents-v1.json")
    policy = GroundingPolicy()
    pipeline = GroundedSQLPipeline(MockSchemaAwareProvider())
    counter = load_local_counter(root / identity["config"]["tokenizer"]["path"])
    audit = []
    for prompt in identity["prompts"]:
        documents = tuple(doc for record in prompt["documents"] for doc in store.load(record["name"]))
        prepared = pipeline.prepare(prompt["question"], root / prompt["database_path"],
                                    db_id=prompt["db_id"], documents=documents, grounding_policy=policy)
        old_tokens = counter(prompt["prompt"])
        prompt.update(prompt=prepared.generation_input.prompt,
                      prompt_version=PROMPT_VERSION, schema_sha256=prepared.schema_hash,
                      prompt_sha256=hashlib.sha256(prepared.generation_input.prompt.encode()).hexdigest(),
                      input_token_reservation=reserve_input_tokens(counter(prepared.generation_input.prompt)))
        audit.append({"example_id": prompt["example_id"], "old_text_tokens": old_tokens,
                      "new_text_tokens": counter(prompt["prompt"]), **prepared.metadata})
    config = identity["config"]
    config.update(plan_id="grounded-prompt-experiment-v2", prompt_variant="grounded_sqlite",
                  prompt_version=PROMPT_VERSION,
                  prompt_change_scope="Instructions, full-schema serialization and bounded value evidence; model/settings/scoring unchanged")
    budget = budget_preflight(identity["prompts"], config, token_counter=counter)
    if not budget["fits_total_caps"] or budget["requests_exceeding_recorded_quota_scenario"]:
        oversized = [r["example_id"] for r in budget["requests"] if not r["fits_recorded_quota_scenario"]]
        raise ValueError(f"Grounded prompts exceed frozen budgets: {sorted(set(oversized))}; no context removed or API calls made")
    identity["mock"] = mock
    identity["prompt_parent_run_sha256"] = original["identity_sha256"]
    identity["resources"].update({p.relative_to(root).as_posix(): sha(p)
                                 for p in sorted((root / "src/text2sql").rglob("*.py"))})
    # Parent source amendments are incorporated in actual new resource hashes.
    manifest = {"identity": identity, "identity_sha256": digest(identity)}
    # Check that no database/resource changed during local preparation.
    load_run(root, source)
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "run.json", manifest)
    write_json(directory / "budget.json", budget)
    write_json(directory / "prompt-audit.json", {
        "provider_calls": 0, "reference_answers_used": False,
        "parent_run_sha256": original["identity_sha256"], "records": audit,
        "records_sha256": digest(audit)})
    write_json(directory / "preflight.json", {
        "provider_calls": 0, "development_examples": len(audit), "prompt_version": PROMPT_VERSION,
        "model_quality_measured": False, "retained_full_schema": True,
        "retained_task_documents": True, "budget": {k: v for k, v in budget.items() if k != "requests"}})
    # Human-readable exact prompt for review, containing no reference answers.
    first = identity["prompts"][0]
    (directory / "example-prompt.txt").write_text(first["prompt"] + "\n", encoding="utf-8")
    load_run(root, directory)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--source-run", type=Path, default=Path("artifacts/development/recovery-live001"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = prepare_grounded_run(args.project_root, args.source_run, args.run_dir, mock=args.mock)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"Stopped: {error}\n")
    print(json.dumps({"provider_calls": 0, "prompt_version": PROMPT_VERSION,
                      "prompts": len(manifest["identity"]["prompts"]),
                      "requests": len(manifest["identity"]["expected_ids"]), "mock": args.mock,
                      "run_dir": str(args.run_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
