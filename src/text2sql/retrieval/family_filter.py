"""Conservative family exclusions over an already verified training index."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .index import LoadedRetrievalIndex, RetrievalIndexError


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def target_family_groups(audit: dict) -> list[dict]:
    targets = sorted(row["db_id"] for row in audit.get("targets", []))
    parents = {"target:" + db: "target:" + db for db in targets}
    def find(node):
        parents.setdefault(node, node)
        while parents[node] != node:
            node = parents[node]
        return node
    def join(left, right):
        parents[find(left)] = find(right)
    for row in audit.get("target_overlaps", []):
        join("target:" + row["left_db_id"], "target:" + row["right_db_id"])
    for row in audit["retrieval_overlaps"]:
        join("target:" + row.get("target_db_id", ""), "train:" + row["training_db_id"])
    groups = {}
    for db in targets:
        groups.setdefault(find("target:" + db), []).append(db)
    return [{"family_id": f"family-{i:03d}", "databases": members}
            for i, members in enumerate(sorted(groups.values()), start=1)]


def filter_schema_families(index: LoadedRetrievalIndex, audit: dict,
                          policy: dict) -> LoadedRetrievalIndex:
    """Exclude all reviewed overlap candidates, independent of the query database.

    The source loader must first verify the original train-only/index firewall.
    This derived index has its own manifest; it never impersonates the source.
    """
    if (policy.get("policy_id") != "recovery-family-exclusions-v1"
            or policy.get("decision") != "exclude_all_overlap_candidates"
            or policy.get("audit_sha256") != digest(audit)):
        raise RetrievalIndexError("Family policy does not match the reviewed audit")
    source_databases = {entry.db_id for entry in index.entries}
    if source_databases != {row["db_id"] for row in audit["training"]}:
        raise RetrievalIndexError("Family audit has different training coverage")
    source_hash = hashlib.sha256("".join(
        json.dumps(entry.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
        for entry in index.entries
    ).encode()).hexdigest()
    if source_hash != policy.get("source_entries_sha256"):
        raise RetrievalIndexError("Family policy has different source entries")
    excluded = policy.get("excluded_training_databases")
    required = sorted({row["training_db_id"] for row in audit["retrieval_overlaps"]})
    if not isinstance(excluded, list) or excluded != required:
        raise RetrievalIndexError("Family exclusions must cover every overlap candidate")
    if policy.get("target_families", []) != target_family_groups(audit):
        raise RetrievalIndexError("Target family groups do not match conservative overlap closure")
    entries = tuple(entry for entry in index.entries if entry.db_id not in excluded)
    if not entries:
        raise RetrievalIndexError("Family exclusions leave no retrieval examples")
    manifest = {
        "index_id": "spider1-train-recovery-family-filtered-v1",
        "source_manifest_sha256": index.manifest_sha256,
        "source_entries_sha256": source_hash, "family_policy_sha256": digest(policy),
        "audit_sha256": digest(audit), "excluded_training_databases": excluded,
        "target_families": target_family_groups(audit),
        "source_examples": len(index.entries), "retained_examples": len(entries),
        "excluded_examples": len(index.entries) - len(entries),
        "entries_sha256": digest([entry.to_dict() for entry in entries]),
    }
    return LoadedRetrievalIndex(entries, manifest, digest(manifest))


def load_family_policy(path: Path) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise RetrievalIndexError("Family policy must be an object")
    return policy
