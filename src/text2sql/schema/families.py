"""Schema-name overlap diagnostics, not proof of identical data or gold answers."""
from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections.abc import Mapping

from text2sql.domain import SchemaSnapshot

FAMILY_AUDIT_VERSION = "schema-family-audit-v1"
Signature = frozenset[tuple[str, str]]


def _identifier(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Schema identifiers must be strings")
    normalized = re.sub(r"[\W_]", "", name.casefold())
    if not normalized:
        raise ValueError("Schema identifier normalizes to an empty string")
    return normalized


def schema_signature(schema: SchemaSnapshot) -> Signature:
    return frozenset((_identifier(table.name), _identifier(column.name))
                     for table in schema.tables for column in table.columns)


def spider1_signatures(records: list[dict], allowed_databases: set[str]) -> dict[str, Signature]:
    signatures = {}
    for record in records:
        db_id = record["db_id"]
        if db_id not in allowed_databases:
            continue
        if db_id in signatures:
            raise ValueError("Duplicate Spider1 schema database")
        tables = record["table_names_original"]
        pairs = []
        for index, column in record["column_names_original"]:
            if type(index) is not int or index < -1 or index >= len(tables):
                raise ValueError("Invalid Spider1 table index")
            if index >= 0:
                pairs.append((_identifier(tables[index]), _identifier(column)))
        signatures[db_id] = frozenset(pairs)
    if set(signatures) != allowed_databases:
        raise ValueError("Training schema coverage is incomplete")
    return signatures


def audit_schema_families(
    targets: Mapping[str, Signature], training: Mapping[str, Signature], *,
    target_splits: Mapping[str, str], min_shared_pairs: int = 5,
    jaccard_threshold: float = 0.3, containment_threshold: float = 0.8,
) -> dict:
    if not targets or not training or set(targets) != set(target_splits):
        raise ValueError("Nonempty schemas and exact target split coverage are required")
    if type(min_shared_pairs) is not int or min_shared_pairs < 1:
        raise ValueError("Minimum overlap must be a positive integer")
    if not (0 < jaccard_threshold <= 1 and 0 < containment_threshold <= 1):
        raise ValueError("Overlap thresholds must be in (0, 1]")
    if any(not pairs for pairs in [*targets.values(), *training.values()]):
        raise ValueError("Empty schemas cannot establish family identity")

    def compare(left: Signature, right: Signature) -> dict | None:
        shared = len(left & right)
        jaccard = shared / len(left | right)
        containment = shared / min(len(left), len(right))
        exact = left == right
        if not exact and not (shared >= min_shared_pairs and (
            jaccard >= jaccard_threshold or containment >= containment_threshold
        )):
            return None
        return {"kind": "exact_normalized_schema" if exact else "potential_family_overlap",
                "shared_pairs": shared, "left_pairs": len(left), "right_pairs": len(right),
                "jaccard": jaccard, "containment": containment}

    retrieval_overlaps = []
    for target, source in itertools.product(sorted(targets), sorted(training)):
        overlap = compare(targets[target], training[source])
        if overlap:
            retrieval_overlaps.append({"target_db_id": target, "training_db_id": source,
                                       "target_split": target_splits[target], **overlap})
    target_overlaps = []
    for left, right in itertools.combinations(sorted(targets), 2):
        overlap = compare(targets[left], targets[right])
        if overlap:
            target_overlaps.append({"left_db_id": left, "right_db_id": right,
                                    "cross_split": target_splits[left] != target_splits[right],
                                    **overlap})

    def records(schemas: Mapping[str, Signature]) -> list[dict]:
        return [{"db_id": name, "pair_count": len(pairs), "schema_name_sha256":
                 hashlib.sha256(json.dumps(sorted(pairs), ensure_ascii=False,
                                           separators=(",", ":")).encode()).hexdigest()}
                for name, pairs in sorted(schemas.items())]

    return {
        "audit_version": FAMILY_AUDIT_VERSION,
        "normalization": "casefold; remove punctuation and underscores; table-column name pairs",
        "policy": {"min_shared_pairs": min_shared_pairs, "jaccard_threshold": jaccard_threshold,
                   "containment_threshold": containment_threshold},
        "limitation": "Name overlap is evidence for review, not identical data or duplicated answers.",
        "targets": records(targets), "training": records(training),
        "retrieval_overlaps": retrieval_overlaps, "target_overlaps": target_overlaps,
        "training_databases_requiring_review": sorted({x["training_db_id"] for x in retrieval_overlaps}),
    }
