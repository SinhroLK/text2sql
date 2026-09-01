from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from text2sql.schema import (
    SCHEMA_LINKER_VERSION,
    RecallSchemaLinkingPolicy,
    SchemaLinkingPolicy,
)
from text2sql.retrieval import (
    RANDOM_RETRIEVAL_VERSION,
    SIMILARITY_RETRIEVAL_VERSION,
)


class ExperimentConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class BaselineExperimentConfig:
    schema_version: int
    experiment_id: str
    baseline: str
    split: str
    prompt_variant: str
    provider: str
    model_id: str
    temperature: float
    max_tokens: int
    seed: int | None
    reasoning_effort: str
    mschema_examples_per_column: int
    mschema_max_text_length: int
    mschema_scan_rows_per_column: int
    schema_linker_version: str | None
    schema_link_max_tables: int
    schema_link_max_columns_per_table: int
    schema_link_minimum_columns_per_table: int
    schema_link_min_score: int
    schema_link_include_value_matches: bool
    schema_link_include_foreign_key_closure: bool
    schema_link_include_all_selected_table_columns: bool
    schema_link_fallback_mode: str | None
    retrieval_index_id: str | None
    retrieval_index_sha256: str | None
    retrieval_manifest_sha256: str | None
    retrieval_strategy: str | None
    retrieval_k: int
    retrieval_seed: int | None
    max_retries: int
    timeout_seconds: float
    config_path: Path
    config_sha256: str


def _required(data: dict[str, Any], key: str, expected: type) -> Any:
    value = data.get(key)
    if not isinstance(value, expected) or (
        isinstance(value, bool) and expected is int
    ):
        raise ExperimentConfigurationError(
            f"Experiment field {key!r} must be {expected.__name__}"
        )
    return value


def load_baseline_config(path: str | Path) -> BaselineExperimentConfig:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ExperimentConfigurationError(f"Experiment config not found: {source}")
    raw = source.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ExperimentConfigurationError(
            "Experiment config is not valid TOML"
        ) from error

    schema_version = _required(data, "schema_version", int)
    experiment_id = _required(data, "experiment_id", str)
    baseline = _required(data, "baseline", str)
    split = _required(data, "split", str)
    prompt_variant = _required(data, "prompt_variant", str)
    provider = _required(data, "provider", str)
    model_id = _required(data, "model_id", str)
    temperature = data.get("temperature")
    timeout_seconds = data.get("timeout_seconds")
    reasoning_effort = _required(data, "reasoning_effort", str)
    seed = data.get("seed")
    if schema_version != 1:
        raise ExperimentConfigurationError(
            "Only experiment schema_version 1 is supported"
        )
    expected_variant = {
        "B0": "question_only",
        "B1": "simple_schema",
        "B2": "mschema",
        "B3": "fewshot_mschema",
        "B4": "fewshot_mschema",
        "B6": "linked_mschema",
        "B6R": "hybrid_linked_mschema",
    }.get(baseline)
    if expected_variant is None or prompt_variant != expected_variant:
        raise ExperimentConfigurationError(
            "Baseline and prompt_variant do not match a supported frozen arm"
        )
    if split != "development":
        raise ExperimentConfigurationError(
            "Baseline experiments may run only on the development split"
        )
    if provider != "groq":
        raise ExperimentConfigurationError(
            "Baseline experiment provider must be groq"
        )
    if not experiment_id.strip() or not model_id.strip():
        raise ExperimentConfigurationError(
            "experiment_id and model_id must not be empty"
        )
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
        raise ExperimentConfigurationError("temperature must be numeric")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(
        timeout_seconds, bool
    ):
        raise ExperimentConfigurationError("timeout_seconds must be numeric")
    if seed is not None and (
        not isinstance(seed, int) or isinstance(seed, bool)
    ):
        raise ExperimentConfigurationError(
            "seed must be an integer or omitted"
        )

    max_tokens = _required(data, "max_tokens", int)
    if reasoning_effort not in ("low", "medium", "high"):
        raise ExperimentConfigurationError(
            "reasoning_effort must be low, medium, or high"
        )
    if baseline in {"B2", "B3", "B4", "B6", "B6R"}:
        mschema_examples_per_column = _required(
            data, "mschema_examples_per_column", int
        )
        mschema_max_text_length = _required(
            data, "mschema_max_text_length", int
        )
        mschema_scan_rows_per_column = _required(
            data, "mschema_scan_rows_per_column", int
        )
        if (
            mschema_examples_per_column < 0
            or mschema_max_text_length <= 0
            or mschema_scan_rows_per_column < mschema_examples_per_column
            or mschema_scan_rows_per_column <= 0
        ):
            raise ExperimentConfigurationError(
                "Invalid M-Schema sampling limits"
            )
    else:
        mschema_examples_per_column = 0
        mschema_max_text_length = 0
        mschema_scan_rows_per_column = 0

    if baseline in {"B6", "B6R"}:
        schema_linker_version = _required(
            data, "schema_linker_version", str
        )
        if schema_linker_version != SCHEMA_LINKER_VERSION:
            raise ExperimentConfigurationError(
                f"schema_linker_version must be {SCHEMA_LINKER_VERSION!r}"
            )
        schema_link_max_tables = _required(
            data, "schema_link_max_tables", int
        )
        schema_link_max_columns_per_table = _required(
            data, "schema_link_max_columns_per_table", int
        )
        schema_link_minimum_columns_per_table = _required(
            data, "schema_link_minimum_columns_per_table", int
        )
        schema_link_min_score = _required(
            data, "schema_link_min_score", int
        )
        schema_link_include_value_matches = _required(
            data, "schema_link_include_value_matches", bool
        )
        schema_link_include_foreign_key_closure = _required(
            data, "schema_link_include_foreign_key_closure", bool
        )
        schema_link_include_all_selected_table_columns = data.get(
            "schema_link_include_all_selected_table_columns", False
        )
        if not isinstance(
            schema_link_include_all_selected_table_columns, bool
        ):
            raise ExperimentConfigurationError(
                "Experiment field "
                "'schema_link_include_all_selected_table_columns' "
                "must be bool"
            )
        if (
            baseline == "B6R"
            and not schema_link_include_all_selected_table_columns
        ):
            raise ExperimentConfigurationError(
                "B6R requires all selected-table columns"
            )
        if (
            baseline == "B6"
            and schema_link_include_all_selected_table_columns
        ):
            raise ExperimentConfigurationError(
                "B6 must retain its frozen column-pruning policy"
            )
        schema_link_fallback_mode = _required(
            data, "schema_link_fallback_mode", str
        )
        try:
            policy_type = (
                RecallSchemaLinkingPolicy
                if schema_link_include_all_selected_table_columns
                else SchemaLinkingPolicy
            )
            policy_type(
                max_tables=schema_link_max_tables,
                max_columns_per_table=schema_link_max_columns_per_table,
                minimum_columns_per_table=schema_link_minimum_columns_per_table,
                min_score=schema_link_min_score,
                include_value_matches=schema_link_include_value_matches,
                include_foreign_key_closure=(
                    schema_link_include_foreign_key_closure
                ),
                fallback_mode=schema_link_fallback_mode,
            )
        except ValueError as error:
            raise ExperimentConfigurationError(
                f"Invalid {baseline} schema-linking policy: {error}"
            ) from error
    else:
        schema_linker_version = None
        schema_link_max_tables = 0
        schema_link_max_columns_per_table = 0
        schema_link_minimum_columns_per_table = 0
        schema_link_min_score = 0
        schema_link_include_value_matches = False
        schema_link_include_foreign_key_closure = False
        schema_link_include_all_selected_table_columns = False
        schema_link_fallback_mode = None

    if baseline in {"B3", "B4"}:
        retrieval_index_id = _required(data, "retrieval_index_id", str)
        retrieval_index_sha256 = _required(
            data, "retrieval_index_sha256", str
        )
        retrieval_manifest_sha256 = _required(
            data, "retrieval_manifest_sha256", str
        )
        retrieval_strategy = _required(data, "retrieval_strategy", str)
        retrieval_k = _required(data, "retrieval_k", int)
        retrieval_seed = data.get("retrieval_seed")
        expected_strategy = (
            RANDOM_RETRIEVAL_VERSION
            if baseline == "B3"
            else SIMILARITY_RETRIEVAL_VERSION
        )
        if retrieval_strategy != expected_strategy:
            raise ExperimentConfigurationError(
                f"{baseline} requires retrieval strategy {expected_strategy!r}"
            )
        if (
            not retrieval_index_id.strip()
            or retrieval_k <= 0
            or not all(
                re.fullmatch(r"[0-9a-f]{64}", value)
                for value in (
                    retrieval_index_sha256,
                    retrieval_manifest_sha256,
                )
            )
        ):
            raise ExperimentConfigurationError(
                "Invalid retrieval index identity, hash, or k"
            )
        if baseline == "B3" and (
            not isinstance(retrieval_seed, int)
            or isinstance(retrieval_seed, bool)
        ):
            raise ExperimentConfigurationError(
                "B3 random retrieval requires an integer retrieval_seed"
            )
        if baseline == "B4" and retrieval_seed is not None:
            raise ExperimentConfigurationError(
                "B4 TF-IDF retrieval must not define retrieval_seed"
            )
    else:
        retrieval_index_id = None
        retrieval_index_sha256 = None
        retrieval_manifest_sha256 = None
        retrieval_strategy = None
        retrieval_k = 0
        retrieval_seed = None

    max_retries = _required(data, "max_retries", int)
    if max_tokens <= 0 or max_retries < 0 or timeout_seconds <= 0:
        raise ExperimentConfigurationError(
            "max_tokens and timeout_seconds must be positive; "
            "max_retries must be non-negative"
        )
    return BaselineExperimentConfig(
        schema_version=schema_version,
        experiment_id=experiment_id,
        baseline=baseline,
        split=split,
        prompt_variant=prompt_variant,
        provider=provider,
        model_id=model_id,
        temperature=float(temperature),
        max_tokens=max_tokens,
        seed=seed,
        mschema_examples_per_column=mschema_examples_per_column,
        mschema_max_text_length=mschema_max_text_length,
        mschema_scan_rows_per_column=mschema_scan_rows_per_column,
        schema_linker_version=schema_linker_version,
        schema_link_max_tables=schema_link_max_tables,
        schema_link_max_columns_per_table=schema_link_max_columns_per_table,
        schema_link_minimum_columns_per_table=(
            schema_link_minimum_columns_per_table
        ),
        schema_link_min_score=schema_link_min_score,
        schema_link_include_value_matches=(
            schema_link_include_value_matches
        ),
        schema_link_include_foreign_key_closure=(
            schema_link_include_foreign_key_closure
        ),
        schema_link_include_all_selected_table_columns=(
            schema_link_include_all_selected_table_columns
        ),
        schema_link_fallback_mode=schema_link_fallback_mode,
        retrieval_index_id=retrieval_index_id,
        retrieval_index_sha256=retrieval_index_sha256,
        retrieval_manifest_sha256=retrieval_manifest_sha256,
        retrieval_strategy=retrieval_strategy,
        retrieval_k=retrieval_k,
        retrieval_seed=retrieval_seed,
        max_retries=max_retries,
        timeout_seconds=float(timeout_seconds),
        config_path=source,
        config_sha256=hashlib.sha256(raw).hexdigest(),
        reasoning_effort=reasoning_effort,
    )
