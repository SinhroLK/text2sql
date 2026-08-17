from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_protocol(protocol: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Validate the frozen DATA-001 protocol and its split firewall."""

    if protocol.get("schema_version") != 1 or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported dataset protocol schema version")
    if protocol.get("protocol_id") != manifest.get("protocol_id"):
        raise ValueError("Protocol and split manifest IDs do not match")

    source = protocol["source"]
    benchmark = protocol["benchmark"]
    split = protocol["split"]
    leakage = protocol["leakage_policy"]
    evaluation = protocol["evaluation"]

    if not COMMIT_PATTERN.fullmatch(source["commit"]):
        raise ValueError("Spider2 source must be pinned to a full commit hash")
    if source["commit"] != manifest.get("source_commit"):
        raise ValueError("Source commit differs between protocol and manifest")

    checksum_fields = (
        "data_sha256",
        "evaluator_sha256",
        "evaluator_utils_sha256",
        "evaluation_manifest_sha256",
    )
    for field in checksum_fields:
        if not SHA256_PATTERN.fullmatch(source[field]):
            raise ValueError(f"Invalid SHA-256 checksum in source.{field}")
    if source["data_sha256"] != manifest.get("source_data_sha256"):
        raise ValueError("Source data checksum differs between protocol and manifest")

    development_ids = manifest["development_instance_ids"]
    test_ids = manifest["test_instance_ids"]
    development_dbs = manifest["development_db_ids"]
    test_dbs = manifest["test_db_ids"]

    if len(development_ids) != benchmark["expected_development_total"]:
        raise ValueError("Unexpected development instance count")
    if len(test_ids) != benchmark["expected_test_total"]:
        raise ValueError("Unexpected test instance count")
    if len(development_ids) + len(test_ids) != benchmark["expected_scope_total"]:
        raise ValueError("Split does not cover the frozen benchmark scope")
    if len(set(development_ids)) != len(development_ids):
        raise ValueError("Duplicate development instance IDs")
    if len(set(test_ids)) != len(test_ids):
        raise ValueError("Duplicate test instance IDs")
    if set(development_ids) & set(test_ids):
        raise ValueError("Development and test instance IDs overlap")
    if set(development_dbs) & set(test_dbs):
        raise ValueError("Development and test databases overlap")
    if not all(instance_id.startswith("local") for instance_id in development_ids + test_ids):
        raise ValueError("Non-SQLite instance found in the SQLite research split")

    if benchmark["official_split_available"]:
        raise ValueError("Spider2-Lite does not publish an official train/dev/test split")
    if benchmark["official_leaderboard_comparable"]:
        raise ValueError("The custom SQLite split cannot be presented as a full leaderboard score")
    if benchmark["oracle_tables"]:
        raise ValueError("Oracle tables must remain disabled for the primary protocol")
    if any(
        leakage[key]
        for key in (
            "gold_sql_in_prompt",
            "gold_sql_in_training",
            "gold_sql_in_retrieval",
            "test_questions_in_retrieval",
            "test_results_visible_during_development",
            "manual_per_test_prompt_edits",
        )
    ):
        raise ValueError("Frozen leakage policy was weakened")
    if not evaluation["require_exact_id_coverage"] or not evaluation["missing_or_extra_predictions_fail_run"]:
        raise ValueError("Evaluation must fail on missing or extra prediction IDs")

    expected_salt = manifest["split_method"]["salt"]
    if split["salt"] != expected_salt:
        raise ValueError("Split salt differs between protocol and manifest")


def load_and_validate_protocol(config_path: Path, project_root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    config_path = config_path.resolve()
    protocol = _load_toml(config_path)
    root = project_root.resolve() if project_root else config_path.parents[2]
    manifest_path = root / protocol["split"]["manifest_path"]
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if manifest_sha256 != protocol["split"]["manifest_sha256"]:
        raise ValueError("Split manifest checksum does not match the frozen protocol")
    manifest = _load_json(manifest_path)
    validate_protocol(protocol, manifest)
    return protocol, manifest
