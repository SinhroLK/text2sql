"""Prepare development inputs and schema-family evidence without a provider call."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from text2sql.datasets import LoadedSpider2LiteDataset, load_spider2_lite_sqlite
from text2sql.datasets.documents import TaskDocumentStore
from text2sql.evaluation.resources import Spider2SQLiteDatabaseResolver, sha256_path
from text2sql.pipeline import Text2SQLPipeline
from text2sql.providers import MockSchemaAwareProvider
from text2sql.retrieval import load_verified_retrieval_index
from text2sql.retrieval.index import load_retrieval_config
from text2sql.retrieval.family_filter import filter_schema_families, load_family_policy
from text2sql.schema import inspect_sqlite_schema
from text2sql.schema.families import audit_schema_families, schema_signature, spider1_signatures

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def validate_development_scope(protocol: dict, dataset: LoadedSpider2LiteDataset) -> None:
    if not isinstance(protocol, dict) or protocol.get("protocol_id") not in {
        "recovery-development-v1", "recovery-development-v2"
    }:
        raise ValueError("Unsupported development protocol")
    if type(protocol.get("schema_version")) is not int or protocol["schema_version"] != 1:
        raise ValueError("Unsupported development protocol schema")
    if protocol.get("status") != "offline_preparation":
        raise ValueError("Development protocol must be in offline preparation")
    if protocol.get("paid_runs_enabled") is not False:
        raise ValueError("This preparation protocol is offline-only")
    if protocol.get("gold_in_generation") is not False:
        raise ValueError("Gold cannot enter generation")
    if protocol.get("retrieval_enabled") is not False:
        raise ValueError("Retrieval requires completed schema-family adjudication")
    for field in ("development_example_ids", "reviewed_test_example_ids"):
        values = protocol.get(field)
        if (not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))):
            raise ValueError(f"{field} must be a list of unique example IDs")
    expected = {example.example_id for example in dataset.for_split("development")}
    ids = protocol.get("development_example_ids", [])
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError("Development scope must match all original development IDs")
    reviewed = {example.example_id for example in dataset.for_split("test")}
    if set(protocol.get("reviewed_test_example_ids", [])) != reviewed:
        raise ValueError("Reviewed-test exposure inventory is incomplete")
    if set(ids) & reviewed:
        raise ValueError("Reviewed test examples cannot enter this development scope")
    if protocol.get("fresh_holdout_status") != "not_reserved":
        raise ValueError("No fresh holdout has been reserved by this protocol")
    if protocol["protocol_id"] == "recovery-development-v2":
        final_ids = protocol.get("final_evaluation_example_ids")
        if (not isinstance(final_ids, list) or final_ids != protocol["reviewed_test_example_ids"]
                or protocol.get("final_evaluation_status") !=
                "reused-set evaluation; no untouched generalization claim"):
            raise ValueError("Recovery v2 must explicitly declare the reviewed final set as reused")
        if protocol.get("family_policy") != "configs/development/family-exclusions-v1.json":
            raise ValueError("Recovery v2 must use the reviewed family policy")


def prepare(root: Path, protocol_path: Path, document_manifest: Path) -> dict:
    root = root.resolve()
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    dataset = load_spider2_lite_sqlite(
        root / "data/raw/spider2/spider2-lite/spider2-lite.jsonl",
        root / "configs/datasets/spider2-lite-sqlite-v1.toml", root,
        root / "configs/datasets/spider2-lite-sqlite-metadata-manifest-v1.json",
    )
    validate_development_scope(protocol, dataset)
    index = load_verified_retrieval_index(
        root / "artifacts/retrieval/spider1-train-v1/retrieval-index.jsonl",
        root / "artifacts/retrieval/spider1-train-v1/retrieval-manifest.json",
        expected_manifest_path=root / "configs/datasets/spider1-train-retrieval-manifest-v1.json",
    )
    index_config = load_retrieval_config(root / "configs/datasets/spider1-train-retrieval-v1.toml")
    tables_path = root / "data/raw/spider1/spider_data/tables.json"
    if sha256_path(tables_path) != index_config.tables_sha256:
        raise ValueError("Spider1 schema checksum mismatch")
    training = spider1_signatures(json.loads(tables_path.read_text(encoding="utf-8")),
                                 {e.db_id for e in index.entries})
    resolver = Spider2SQLiteDatabaseResolver(
        root / "data/raw/spider2/spider2-lite/resource/databases/spider2-localdb"
    )
    db_splits = {example.db_id: example.split for example in dataset.examples}
    databases = {db: resolver.resolve(db) for db in sorted(db_splits)}
    schemas = {db: inspect_sqlite_schema(value.path, db_id=db) for db, value in databases.items()}
    audit = audit_schema_families(
        {db: schema_signature(schema) for db, schema in schemas.items()}, training,
        target_splits=db_splits,
    )
    audit["resource_hashes"] = {
        "spider1_tables": index_config.tables_sha256,
        "retrieval_index": sha256_path(root / "artifacts/retrieval/spider1-train-v1/retrieval-index.jsonl"),
        "databases": {db: value.sha256 for db, value in databases.items()},
    }
    audit["databases_without_declared_foreign_keys"] = [
        db for db, schema in schemas.items() if not any(t.foreign_keys for t in schema.tables)
    ]
    filtered_manifest = None
    if protocol["protocol_id"] == "recovery-development-v2":
        filtered_manifest = filter_schema_families(
            index, audit, load_family_policy(root / protocol["family_policy"])
        ).manifest
    store = TaskDocumentStore.from_manifest(
        root / "data/raw/spider2/spider2-lite/resource/documents", document_manifest,
    )
    pipeline = Text2SQLPipeline(MockSchemaAwareProvider())
    prompts = []
    for example in sorted(dataset.for_split("development"), key=lambda e: e.example_id):
        documents = store.load(example.metadata.get("external_knowledge"))
        prepared = pipeline.prepare(example.question, databases[example.db_id].path,
                                    db_id=example.db_id, documents=documents)
        prompt = prepared.generation_input.prompt
        prompts.append({"example_id": example.example_id, "db_id": example.db_id,
                        "question": example.question, "prompt": prompt,
                        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                        "prompt_version": prepared.prompt_version,
                        "schema_sha256": prepared.schema_hash,
                        "documents": prepared.metadata["documents"]})
    return {"protocol_id": protocol["protocol_id"], "provider_calls": 0,
            "prediction_sql_executions": 0, "development_examples": len(prompts),
            "examples_with_documents": sum(bool(p["documents"]) for p in prompts),
            "paid_run_ready": False,
            "remaining_gates": (["current account quota/pricing confirmation",
                                  "explicit user authorization before provider calls"]
                                if filtered_manifest else
                                ["schema-family adjudication and enforced retrieval exclusion",
                                 "token/quota/cost preflight and versioned run selection policy",
                                 "fresh-holdout reservation or explicit reused-set final protocol"]),
            "protocol_sha256": sha256_path(protocol_path),
            "document_manifest_sha256": sha256_path(document_manifest),
            "runtime": {"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version},
            "source_hashes": {p.relative_to(root).as_posix(): sha256_path(p)
                              for p in sorted((root / "src/text2sql").rglob("*.py"))},
            "family_audit": audit, "filtered_retrieval_manifest": filtered_manifest,
            "prompts": prompts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--document-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if args.output_dir.exists():
        parser.error("Output directory already exists; select a new audit directory")
    result = prepare(
        root, args.protocol or root / "configs/development/recovery-development-v1.json",
        args.document_manifest or root / "configs/development/task-documents-v1.json",
    )
    prompts = result.pop("prompts")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "preflight.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output_dir / "prompts.jsonl").write_text("".join(
        json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" for p in prompts
    ), encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "protocol_id", "provider_calls", "development_examples", "examples_with_documents", "paid_run_ready",
    )}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
