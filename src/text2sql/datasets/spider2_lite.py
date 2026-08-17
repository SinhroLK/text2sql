from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from text2sql.domain.models import Text2SQLExample

from .protocol import load_and_validate_protocol


DATASET_MANIFEST_SCHEMA_VERSION = 1
DATASET_ID = "spider2-lite-sqlite-metadata-v1"
NORMALIZED_EXAMPLES_FILENAME = "examples.jsonl"
DATASET_MANIFEST_FILENAME = "dataset-manifest.json"
PROHIBITED_GOLD_FIELDS = frozenset({"sql", "query", "gold_sql", "gold_query"})


@dataclass(frozen=True)
class LoadedSpider2LiteDataset:
    """Validated metadata-only view of the frozen Spider2-Lite SQLite scope."""

    examples: tuple[Text2SQLExample, ...]
    manifest: dict[str, Any]

    def for_split(self, split: str) -> tuple[Text2SQLExample, ...]:
        if split not in {"development", "test"}:
            raise ValueError("split must be 'development' or 'test'")
        return tuple(example for example in self.examples if example.split == split)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_non_empty_string(record: dict[str, Any], field: str, line_number: int) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Spider2 line {line_number} has invalid {field!r}")
    return value


def _load_upstream_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ValueError(f"Spider2 source contains a blank line at {line_number}")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid Spider2 JSON on line {line_number}: {error.msg}") from error
            if not isinstance(record, dict):
                raise ValueError(f"Spider2 line {line_number} is not a JSON object")

            prohibited = PROHIBITED_GOLD_FIELDS.intersection(record)
            if prohibited:
                fields = ", ".join(sorted(prohibited))
                raise ValueError(f"Spider2 metadata source unexpectedly contains gold-like fields: {fields}")

            instance_id = _require_non_empty_string(record, "instance_id", line_number)
            _require_non_empty_string(record, "db", line_number)
            _require_non_empty_string(record, "question", line_number)
            external_knowledge = record.get("external_knowledge")
            if external_knowledge is not None and not isinstance(external_knowledge, str):
                raise ValueError(
                    f"Spider2 line {line_number} has invalid 'external_knowledge'; expected string or null"
                )
            if instance_id in seen_ids:
                raise ValueError(f"Duplicate Spider2 instance_id: {instance_id}")
            seen_ids.add(instance_id)
            records.append(record)

    return records


def _platform_counts(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {"bigquery": 0, "snowflake": 0, "sqlite": 0}
    for record in records:
        instance_id = record["instance_id"]
        if instance_id.startswith(("bq", "ga")):
            counts["bigquery"] += 1
        elif instance_id.startswith(("sf", "sf_bq")):
            counts["snowflake"] += 1
        elif instance_id.startswith("local"):
            counts["sqlite"] += 1
        else:
            raise ValueError(f"Unknown Spider2 execution platform for instance_id {instance_id}")
    return counts


def _normalized_example(example: Text2SQLExample) -> dict[str, Any]:
    return {
        "db_id": example.db_id,
        "dialect": example.dialect,
        "example_id": example.example_id,
        "metadata": example.metadata,
        "question": example.question,
        "split": example.split,
    }


def serialize_examples(examples: Iterable[Text2SQLExample]) -> bytes:
    lines = [
        json.dumps(_normalized_example(example), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for example in examples
    ]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _build_dataset_manifest(
    *,
    examples: tuple[Text2SQLExample, ...],
    protocol: dict[str, Any],
    split_manifest: dict[str, Any],
    platform_counts: dict[str, int],
) -> dict[str, Any]:
    development = tuple(example for example in examples if example.split == "development")
    test = tuple(example for example in examples if example.split == "test")
    normalized_bytes = serialize_examples(examples)

    return {
        "schema_version": DATASET_MANIFEST_SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "protocol_id": protocol["protocol_id"],
        "source": {
            "repository_url": protocol["source"]["repository_url"],
            "commit": protocol["source"]["commit"],
            "data_path": protocol["source"]["data_path"],
            "data_sha256": protocol["source"]["data_sha256"],
        },
        "scope": {
            "name": protocol["benchmark"]["name"],
            "label": protocol["evaluation"]["headline_label"],
            "dialect": "sqlite",
            "split_type": "custom database-disjoint development/test split",
            "oracle_tables": False,
            "contains_gold_sql": False,
            "contains_database_files": False,
        },
        "counts": {
            "upstream_total": sum(platform_counts.values()),
            "upstream_by_platform": platform_counts,
            "selected_total": len(examples),
            "development_examples": len(development),
            "test_examples": len(test),
            "development_databases": len(split_manifest["development_db_ids"]),
            "test_databases": len(split_manifest["test_db_ids"]),
        },
        "database_ids": {
            "development": sorted(split_manifest["development_db_ids"]),
            "test": sorted(split_manifest["test_db_ids"]),
        },
        "artifacts": {
            "examples_filename": NORMALIZED_EXAMPLES_FILENAME,
            "examples_sha256": hashlib.sha256(normalized_bytes).hexdigest(),
            "record_format": "JSON Lines; UTF-8; keys sorted; compact separators; trailing newline",
        },
        "fields": ["example_id", "db_id", "question", "dialect", "split", "metadata"],
        "metadata_fields": ["external_knowledge", "source_instance_id", "source_commit"],
        "policies": {
            "gold_sql_allowed": False,
            "spider2_examples_allowed_in_retrieval": False,
            "test_examples_visible_during_development": False,
        },
    }


def validate_dataset_manifest(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    if actual != expected:
        actual_hash = hashlib.sha256(_json_bytes(actual)).hexdigest()
        expected_hash = hashlib.sha256(_json_bytes(expected)).hexdigest()
        raise ValueError(
            "Generated dataset manifest does not match the frozen DATA-003 manifest "
            f"(actual {actual_hash}, expected {expected_hash})"
        )


def load_spider2_lite_sqlite(
    source_jsonl: Path,
    config_path: Path,
    project_root: Path | None = None,
    expected_manifest_path: Path | None = None,
) -> LoadedSpider2LiteDataset:
    """Load only frozen Spider2-Lite SQLite metadata after all DATA-001 checks."""

    source_jsonl = source_jsonl.resolve()
    if not source_jsonl.is_file():
        raise FileNotFoundError(f"Spider2-Lite source JSONL not found: {source_jsonl}")

    protocol, split_manifest = load_and_validate_protocol(config_path, project_root)

    actual_source_sha256 = sha256_file(source_jsonl)
    expected_source_sha256 = protocol["source"]["data_sha256"]
    if actual_source_sha256 != expected_source_sha256:
        raise ValueError(
            "Spider2-Lite source checksum mismatch: "
            f"got {actual_source_sha256}, expected {expected_source_sha256}"
        )

    records = _load_upstream_records(source_jsonl)
    benchmark = protocol["benchmark"]
    if len(records) != benchmark["expected_upstream_total"]:
        raise ValueError("Unexpected number of records in pinned Spider2-Lite source")

    platform_counts = _platform_counts(records)
    expected_platform_counts = {
        "bigquery": protocol["source"]["pinned_bigquery_total"],
        "snowflake": protocol["source"]["pinned_snowflake_total"],
        "sqlite": protocol["source"]["pinned_sqlite_total"],
    }
    if platform_counts != expected_platform_counts:
        raise ValueError("Pinned Spider2-Lite platform counts do not match DATA-001")

    development_ids = set(split_manifest["development_instance_ids"])
    test_ids = set(split_manifest["test_instance_ids"])
    selected_ids = development_ids | test_ids
    development_dbs = set(split_manifest["development_db_ids"])
    test_dbs = set(split_manifest["test_db_ids"])

    local_ids = {record["instance_id"] for record in records if record["instance_id"].startswith("local")}
    if local_ids != selected_ids:
        missing = sorted(selected_ids - local_ids)
        unexpected = sorted(local_ids - selected_ids)
        raise ValueError(
            "DATA-001 split does not exactly cover pinned SQLite instances; "
            f"missing={missing}, unexpected={unexpected}"
        )

    examples: list[Text2SQLExample] = []
    for record in records:
        instance_id = record["instance_id"]
        if instance_id not in selected_ids:
            continue

        db_id = record["db"]
        split = "development" if instance_id in development_ids else "test"
        allowed_dbs = development_dbs if split == "development" else test_dbs
        if db_id not in allowed_dbs:
            raise ValueError(f"Instance {instance_id} points to database {db_id!r} outside its frozen split")

        examples.append(
            Text2SQLExample(
                example_id=instance_id,
                db_id=db_id,
                question=record["question"],
                dialect="sqlite",
                split=split,
                gold_sql=None,
                gold_result_path=None,
                metadata={
                    "external_knowledge": record.get("external_knowledge"),
                    "source_commit": protocol["source"]["commit"],
                    "source_instance_id": instance_id,
                },
            )
        )

    frozen_examples = tuple(sorted(examples, key=lambda example: example.example_id))
    manifest = _build_dataset_manifest(
        examples=frozen_examples,
        protocol=protocol,
        split_manifest=split_manifest,
        platform_counts=platform_counts,
    )

    if expected_manifest_path is not None:
        expected = json.loads(expected_manifest_path.read_text(encoding="utf-8"))
        validate_dataset_manifest(manifest, expected)

    return LoadedSpider2LiteDataset(examples=frozen_examples, manifest=manifest)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_artifact(path: Path, content: bytes, overwrite: bool) -> None:
    if path.exists():
        if path.read_bytes() == content:
            return
        if not overwrite:
            raise FileExistsError(f"Refusing to replace different artifact without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_bytes(content)
    temporary_path.replace(path)


def write_processed_dataset(
    dataset: LoadedSpider2LiteDataset,
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    """Write deterministic metadata artifacts; never writes SQL or database files."""

    output_dir = output_dir.resolve()
    examples_path = output_dir / NORMALIZED_EXAMPLES_FILENAME
    manifest_path = output_dir / DATASET_MANIFEST_FILENAME
    _write_artifact(examples_path, serialize_examples(dataset.examples), overwrite)
    _write_artifact(manifest_path, _json_bytes(dataset.manifest), overwrite)
    return examples_path, manifest_path
