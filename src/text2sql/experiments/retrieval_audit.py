from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from text2sql.datasets import LoadedSpider2LiteDataset
from text2sql.retrieval import (
    LoadedRetrievalIndex,
    build_retrieval_selector,
)

from .config import BaselineExperimentConfig


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_index_identity(
    config: BaselineExperimentConfig,
    index: LoadedRetrievalIndex,
) -> None:
    manifest = index.manifest
    if (
        manifest.get("index_id") != config.retrieval_index_id
        or manifest.get("artifact", {}).get("sha256")
        != config.retrieval_index_sha256
        or index.manifest_sha256 != config.retrieval_manifest_sha256
    ):
        raise ValueError(
            "Retrieval index identity does not match the frozen experiment config"
        )


def audit_development_retrieval(
    *,
    config: BaselineExperimentConfig,
    dataset: LoadedSpider2LiteDataset,
    retrieval_index: LoadedRetrievalIndex,
) -> dict[str, Any]:
    """Audit every development retrieval decision without invoking a provider."""

    if config.baseline not in {"B3", "B4"} or config.split != "development":
        raise ValueError(
            "Retrieval audit requires a B3 or B4 development configuration"
        )
    _validate_index_identity(config, retrieval_index)
    selector = build_retrieval_selector(
        retrieval_index,
        strategy=config.retrieval_strategy or "",
        k=config.retrieval_k,
        seed=config.retrieval_seed,
    )
    examples = tuple(
        sorted(
            dataset.for_split("development"),
            key=lambda item: item.example_id,
        )
    )
    if not examples:
        raise ValueError("Development split is empty")
    if len({item.example_id for item in examples}) != len(examples):
        raise ValueError("Development example IDs are not unique")

    records: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    selected_database_ids: list[str] = []
    for example in examples:
        selection = selector.select(example.question)
        selected = [item.to_dict() for item in selection.entries]
        selected_ids.extend(str(item["retrieval_id"]) for item in selected)
        selected_database_ids.extend(str(item["db_id"]) for item in selected)
        records.append(
            {
                "target_example_id": example.example_id,
                "target_db_id": example.db_id,
                "target_question_sha256": _sha256(example.question),
                **selection.to_dict(),
            }
        )

    frequency = Counter(selected_ids)
    return {
        "schema_version": 1,
        "scope": "development",
        "experiment": {
            "experiment_id": config.experiment_id,
            "config_sha256": config.config_sha256,
            "baseline": config.baseline,
            "prompt_variant": config.prompt_variant,
        },
        "retrieval_policy": {
            "index_id": config.retrieval_index_id,
            "index_sha256": config.retrieval_index_sha256,
            "manifest_sha256": config.retrieval_manifest_sha256,
            "strategy": config.retrieval_strategy,
            "k": config.retrieval_k,
            "seed": config.retrieval_seed,
        },
        "retrieval_manifest": retrieval_index.manifest,
        "summary": {
            "targets": len(records),
            "target_databases": len({item.db_id for item in examples}),
            "selections": len(selected_ids),
            "unique_retrieval_ids": len(frequency),
            "unique_retrieval_databases": len(set(selected_database_ids)),
            "maximum_retrieval_id_frequency": max(frequency.values()),
            "exact_target_id_coverage": True,
        },
        "records": records,
    }
