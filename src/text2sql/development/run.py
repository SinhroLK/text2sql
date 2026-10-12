"""Prepare, generate/resume, and score the C0/C1/C2 development experiment."""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time
import tomllib

from text2sql.domain import GenerationInput
from text2sql.providers import GroqProvider, MockSchemaAwareProvider
from text2sql.schema import inspect_sqlite_schema

from .controls import DeferredRequest, Limits, RequestJournal, digest
from .offline import budget_preflight
from .preflight import PROJECT_ROOT, prepare
from .provider import JournaledProvider
from .tokenization import load_local_counter, reserve_input_tokens


def sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def environment() -> dict:
    import sqlite3
    return {"python": sys.version, "sqlite": sqlite3.sqlite_version,
            "packages": {name: version(name) for name in ("groq", "tiktoken", "pandas", "sqlparse")}}


def prepare_run(root: Path, directory: Path, *, tokens_per_minute=8000,
                tokens_per_day=200000, mock=False) -> dict:
    if directory.exists():
        raise ValueError("Run directory exists; use generate to resume, or a fresh prepare directory")
    if any(type(x) is not int or x <= 0 for x in (tokens_per_minute, tokens_per_day)):
        raise ValueError("Account token limits must be positive integers")
    config_path = root / "configs/development/free-tier-run-v1.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config["paid_runs_enabled"] is not False or config["account_mode"] != "free"
            or config["provider_max_retries"] != 0):
        raise ValueError("This runner supports the free-plan configuration only")
    preflight = prepare(root, root / config["selection_protocol"],
                        root / "configs/development/task-documents-v1.json")
    prompts = preflight.pop("prompts")
    counter = load_local_counter(root / config["tokenizer"]["path"])
    config["quota_scenario"].update(tokens_per_minute=int(tokens_per_minute * 0.9),
                                    tokens_per_day=int(tokens_per_day * 0.9))
    budget = budget_preflight(prompts, config, token_counter=counter)
    if not budget["fits_total_caps"] or budget["requests_exceeding_recorded_quota_scenario"]:
        raise ValueError("Requests do not fit the declared quota/budget; no run created or call made")
    for prompt in prompts:
        prompt["input_token_reservation"] = reserve_input_tokens(counter(prompt["prompt"]))
    database_root = root / "data/raw/spider2/spider2-lite/resource/databases/spider2-localdb"
    from text2sql.evaluation import Spider2SQLiteDatabaseResolver
    resolver = Spider2SQLiteDatabaseResolver(database_root)
    resources = {}
    for prompt in prompts:
        database = resolver.resolve(prompt["db_id"])
        prompt["database_path"] = database.path.relative_to(root).as_posix()
        resources[prompt["database_path"]] = database.sha256
        if database.path.stat().st_size > config["execution_limits"]["max_database_bytes"]:
            raise ValueError("Development database exceeds frozen scoring size limit")
    for name in ("free-tier-run-v1.json", "recovery-development-v2.json", "task-documents-v1.json",
                 "family-exclusions-v1.json"):
        relative = "configs/development/" + name
        resources[relative] = sha(root / relative)
    resources.update(preflight["source_hashes"])
    # Pin evaluation resources before outcomes exist; hashing is not prompt input.
    protocol_path = root / "configs/datasets/spider2-lite-sqlite-v1.toml"
    protocol = tomllib.loads(protocol_path.read_text(encoding="utf-8"))
    upstream = root / "data/raw/spider2"
    for path_key, hash_key in (("evaluation_manifest_path", "evaluation_manifest_sha256"),
                               ("evaluator_utils_path", "evaluator_utils_sha256")):
        path = upstream / protocol["source"][path_key]
        if sha(path) != protocol["source"][hash_key]:
            raise ValueError("Pinned evaluator resource changed")
        resources[path.relative_to(root).as_posix()] = sha(path)
    gold_root = upstream / "spider2-lite/evaluation_suite/gold/exec_result"
    gold_files = {}
    for prompt in prompts:
        identifier = prompt["example_id"]
        matches = sorted([*gold_root.glob(identifier + ".csv"), *gold_root.glob(identifier + "_*.csv")])
        if not matches:
            raise ValueError(f"Missing gold resources for {identifier}")
        gold_files[identifier] = [p.relative_to(root).as_posix() for p in matches]
        resources.update({p.relative_to(root).as_posix(): sha(p) for p in matches})
    resources[config["tokenizer"]["path"]] = sha(root / config["tokenizer"]["path"])
    documents = json.loads((root / "configs/development/task-documents-v1.json").read_text())
    for name, checksum in documents["documents"].items():
        resources["data/raw/spider2/spider2-lite/resource/documents/" + name] = checksum
    rates, quota = config["planning_rate_ceilings"], config["quota_scenario"]
    limits = Limits(config["max_calls"], config["max_total_tokens"], quota["tokens_per_minute"],
                    quota["tokens_per_day"], config["max_attempts_per_example"], config["max_cost_usd"],
                    rates["input_usd_per_million"], rates["output_usd_per_million"])
    keys = [arm["arm"] + ":" + p["example_id"] for arm in config["arms"] for p in prompts]
    identity = {"runner_version": "recovery-free-run-v1", "expected_ids": keys,
                "config": config, "prompts": prompts, "resources": resources,
                "gold_files": gold_files, "environment": environment(), "mock": mock,
                "limits": asdict(limits), "target_families": preflight["filtered_retrieval_manifest"]["target_families"]}
    manifest = {"identity": identity, "identity_sha256": digest(identity)}
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "run.json", manifest)
    write_json(directory / "budget.json", budget)
    write_json(directory / "preflight.json", preflight)
    return manifest


def load_run(root: Path, directory: Path) -> dict:
    manifest = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    identity = manifest["identity"]
    if manifest["identity_sha256"] != digest(identity):
        raise ValueError("Run identity checksum mismatch")
    if identity["runner_version"] != "recovery-free-run-v1" or identity["environment"] != environment():
        raise ValueError("Run version/environment changed; restore the prepared environment")
    resources = dict(identity["resources"])
    amendment_path = directory / "quota-accounting-amendment.json"
    if amendment_path.exists():
        amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
        details = amendment["details"]
        originals = {
            "src/text2sql/development/controls.py": "1d5fd052c6fba4a7d41f45f22cfde9c8c3f9ab811fa61547196294a9bd43d2d4",
            "src/text2sql/development/run.py": "ce5ea3a6bbce5bb9dd3fc82e9b7ddd1749455fdee90d8c1da479c3c622ed42cb",
        }
        if (amendment["sha256"] != digest(details)
                or details["run_sha256"] != manifest["identity_sha256"]
                or details["policy"] != "reported-daily-usage-v1"
                or details["original_sources"] != originals
                or set(details["patched_sources"]) != set(originals)
                or any(resources.get(name) != checksum for name, checksum in originals.items())):
            raise ValueError("Invalid quota accounting amendment")
        with (directory / "requests.jsonl").open("rb") as stream:
            prefix = stream.read(details["journal_prefix_bytes"])
        if (len(prefix) != details["journal_prefix_bytes"]
                or hashlib.sha256(prefix).hexdigest() != details["journal_prefix_sha256"]):
            raise ValueError("Amended journal prefix changed")
        resources.update(details["patched_sources"])
    for name, checksum in resources.items():
        path = (root / name).resolve()
        path.relative_to(root.resolve())
        if sha(path) != checksum:
            raise ValueError(f"Frozen resource changed: {name}; restore it before resuming")
    return manifest


def generate_records(identity: dict, journal: RequestJournal, request_factory, provider_factory,
                     *, allow_api=False, free_plan_confirmed=False, wait=False,
                     clock=time.time, sleep=time.sleep, progress=print) -> list[dict] | None:
    records = []
    for arm in identity["config"]["arms"]:
        provider = provider_factory(arm)
        for prompt in identity["prompts"]:
            key = arm["arm"] + ":" + prompt["example_id"]
            adapter = JournaledProvider(
                provider, journal, example_id=key, expected_prompt_sha256=prompt["prompt_sha256"],
                max_completion_tokens=arm["max_completion_tokens"],
                token_counter=lambda text, count=prompt["input_token_reservation"]: count,
                user_authorized_live_calls=allow_api, free_plan_confirmed=free_plan_confirmed)
            request = request_factory(prompt, provider)
            while True:
                try:
                    previous_calls = sum(e["event"] == "reserve" for e in journal.events)
                    response = adapter.generate(request)
                    if response.metadata["journal_status"] == "known_rejected":
                        continue  # journal enforces delay and at most one retry
                    if (response.metadata.get("http_status") in (401, 403)
                            and sum(e["event"] == "reserve" for e in journal.events) > previous_calls):
                        # One refused request is retained; do not burn the whole run
                        # on invalid credentials or unavailable account permissions.
                        raise ValueError("Groq refused credentials/access. The failed attempt is retained; no further calls made")
                    break
                except DeferredRequest as error:
                    progress(json.dumps({"event": "paused", "key": key,
                                         "retry_after_seconds": round(error.seconds, 1),
                                         "retry_after_hours": round(error.seconds / 3600, 2),
                                         "resume_at_unix": clock() + error.seconds,
                                         "action": "wait" if wait else "rerun the same generate command later"}))
                    if not wait:
                        return None
                    deadline = clock() + error.seconds
                    while clock() < deadline:
                        sleep(min(30, deadline - clock()))
            record = {"key": key, "arm": arm["arm"], "example_id": prompt["example_id"],
                      "db_id": prompt["db_id"], "prompt_sha256": prompt["prompt_sha256"],
                      "status": response.metadata["journal_status"],
                      "generated_sql": response.candidates[0] if response.candidates else None,
                      "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                      "metadata": response.metadata}
            records.append(record)
            progress(json.dumps({"event": "generation", "key": key, "status": record["status"],
                                 "completed": len(records), "total": len(identity["expected_ids"])}))
    return records


def generate_run(root: Path, directory: Path, *, allow_api=False,
                 free_plan_confirmed=False, wait=False) -> int:
    manifest = load_run(root, directory)
    identity = manifest["identity"]
    if not identity["mock"]:
        if not allow_api or not free_plan_confirmed:
            raise ValueError("Use --allow-api --confirm-free-plan after checking this key belongs to your Free organization")
        if not os.environ.get("GROQ_API_KEY"):
            raise ValueError("Set GROQ_API_KEY in your shell; it is never stored in the run")
    def provider_factory(arm):
        if identity["mock"]:
            return MockSchemaAwareProvider()
        config = identity["config"]
        return GroqProvider(model_id=config["model_id"], temperature=config["temperature"],
                            seed=config["seed"], reasoning_effort=arm["reasoning_effort"],
                            max_tokens=arm["max_completion_tokens"], max_retries=0)
    def request_factory(prompt, provider):
        schema = inspect_sqlite_schema(root / prompt["database_path"], db_id=prompt["db_id"])
        return GenerationInput(prompt["question"], prompt["prompt"], schema, provider.model_id)
    with RequestJournal(directory / "requests.jsonl", {"expected_ids": identity["expected_ids"],
                        "run_sha256": manifest["identity_sha256"]}, Limits(**identity["limits"])) as journal:
        if (directory / "generation.json").exists():
            validate_generation(directory, manifest, journal)
            print("Generation already complete; no calls made. Run the score command.")
            return 0
        # The mock harness has a virtual clock, so quota deferrals take no real time.
        options = {}
        if identity["mock"]:
            instant = [time.time()]
            journal.clock = lambda: instant[0]
            options = {"clock": journal.clock, "sleep": lambda delay: instant.__setitem__(0, instant[0] + delay)}
        records = generate_records(identity, journal, request_factory, provider_factory,
                                   allow_api=allow_api, free_plan_confirmed=free_plan_confirmed,
                                   wait=wait or identity["mock"], **options)
        if records is None:
            return 75
        write_json(directory / "generation.json", {"run_sha256": manifest["identity_sha256"],
                    "journal_sha256": sha(directory / "requests.jsonl"), "mock": identity["mock"],
                    "records": records, "records_sha256": digest(records)})
    return 0


def validate_generation(directory: Path, manifest: dict, journal: RequestJournal) -> dict:
    generation = json.loads((directory / "generation.json").read_text(encoding="utf-8"))
    records = generation["records"]
    identity = manifest["identity"]
    if (generation["run_sha256"] != manifest["identity_sha256"] or generation["mock"] != identity["mock"]
            or generation["journal_sha256"] != sha(directory / "requests.jsonl")
            or generation["records_sha256"] != digest(records)
            or [r["key"] for r in records] != identity["expected_ids"]):
        raise ValueError("Generation is incomplete or differs from its frozen run/journal")
    # Reconstruct final SQL from durable outcomes, so edited predictions cannot
    # pass merely by recomputing the prediction-file checksum.
    for record in records:
        arm_name, example_id = record["key"].split(":", 1)
        prompt = next(p for p in identity["prompts"] if p["example_id"] == example_id)
        if (record["arm"] != arm_name or record["example_id"] != example_id
                or record["db_id"] != prompt["db_id"] or record["prompt_sha256"] != prompt["prompt_sha256"]):
            raise ValueError("Prediction example/schema/prompt identity mismatch")
        attempts = [e for e in journal.events if e["event"] == "reserve" and e["example_id"] == record["key"]]
        if not attempts:
            raise ValueError("Prediction has no reserved attempt")
        outcomes = [e["result"] for e in journal.events if e["event"] == "outcome"
                    and e["reservation"] == attempts[-1]["number"]]
        outcome = outcomes[-1] if outcomes else {"status": "interrupted"}
        sql = outcome["candidates"][0] if outcome["status"] == "complete" else None
        if record["generated_sql"] != sql or (record["status"] == "complete") != (sql is not None):
            raise ValueError("Prediction differs from durable provider outcome")
    return generation


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "generate", "score", "status"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--tokens-per-minute", type=int, help="prepare only; account limit, default 8000")
    parser.add_argument("--tokens-per-day", type=int, help="prepare only; account limit, default 200000")
    parser.add_argument("--mock", action="store_true", help="prepare a separate provider-free rehearsal")
    parser.add_argument("--allow-api", action="store_true", help="explicitly enable model calls")
    parser.add_argument("--confirm-free-plan", action="store_true", help="attest the API key belongs to a Free organization with the declared limits")
    parser.add_argument("--wait", action="store_true", help="wait for quotas; otherwise exit 75 and resume later")
    args = parser.parse_args(argv)
    if args.command != "prepare" and (args.mock or args.tokens_per_minute is not None or args.tokens_per_day is not None):
        parser.error("--mock and account token limits are prepare-only; an existing run keeps its frozen settings")
    if args.command != "generate" and (args.allow_api or args.confirm_free_plan or args.wait):
        parser.error("API confirmation and --wait are generate-only options")
    root, directory = args.project_root.resolve(), args.run_dir.resolve()
    try:
        if args.command == "prepare":
            prepared = prepare_run(root, directory,
                                   tokens_per_minute=8000 if args.tokens_per_minute is None else args.tokens_per_minute,
                                   tokens_per_day=200000 if args.tokens_per_day is None else args.tokens_per_day, mock=args.mock)
            print(json.dumps({"prepared": str(directory), "requests": len(prepared["identity"]["expected_ids"]),
                              "mock": args.mock, "provider_calls": 0}))
            return 0
        if args.command == "generate":
            return generate_run(root, directory, allow_api=args.allow_api,
                                free_plan_confirmed=args.confirm_free_plan, wait=args.wait)
        manifest = load_run(root, directory)
        if args.command == "score":
            from .scoring import score_run
            return score_run(root, directory, manifest)
        path = directory / "requests.jsonl"
        events = [json.loads(line)["data"] for line in path.read_text().splitlines()] if path.exists() else []
        print(json.dumps({"mock": manifest["identity"]["mock"],
                          "reserved_attempts": sum(e["event"] == "reserve" for e in events),
                          "generation_complete": (directory / "generation.json").exists(),
                          "scoring_complete": (directory / "scores.json").exists()}))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Resume using the same run directory; recorded calls will not be repeated.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
