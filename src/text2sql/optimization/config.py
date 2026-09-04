from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from text2sql.experiments.config import (
    BaselineExperimentConfig,
    ExperimentConfigurationError,
    load_baseline_config,
)


class B5ConfigurationError(ValueError):
    """Invalid frozen DSPY-001/B5 optimization configuration."""


@dataclass(frozen=True)
class B5OptimizationConfig:
    schema_version: int
    optimization_id: str
    baseline: str
    split: str
    program_version: str
    dspy_version: str
    litellm_version: str
    optuna_version: str
    optimizer: str
    optimizer_auto: str
    num_candidates: int
    num_trials: int
    max_bootstrapped_demos: int
    max_labeled_demos: int
    minibatch: bool
    num_threads: int
    max_errors: int
    optimizer_seed: int
    dspy_model_id: str
    cache: bool
    cache_schema_version: int
    cache_size_limit_bytes: int
    cache_resume_max_age_hours: int
    tokens_per_minute: int
    token_safety_margin: float
    rate_limit_window_seconds: float
    rate_limit_buffer_seconds: float
    rate_limit_max_retries: int
    program_aware_proposer: bool
    data_aware_proposer: bool
    tip_aware_proposer: bool
    fewshot_aware_proposer: bool
    train_database_ids: tuple[str, ...]
    validation_database_ids: tuple[str, ...]
    base_config: BaselineExperimentConfig
    base_config_sha256: str
    config_path: Path
    config_sha256: str


def _required(data: dict[str, Any], key: str, expected: type) -> Any:
    value = data.get(key)
    if not isinstance(value, expected) or (
        expected is int and isinstance(value, bool)
    ):
        raise B5ConfigurationError(
            f"B5 field {key!r} must be {expected.__name__}"
        )
    if expected is str and not value.strip():
        raise B5ConfigurationError(f"B5 field {key!r} must not be empty")
    return value


def _number(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise B5ConfigurationError(f"B5 field {key!r} must be numeric")
    return float(value)


def _string_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = _required(data, key, list)
    if not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise B5ConfigurationError(
            f"B5 field {key!r} must contain non-empty strings"
        )
    if len(set(value)) != len(value):
        raise B5ConfigurationError(f"B5 field {key!r} contains duplicates")
    return tuple(value)


def load_b5_optimization_config(
    path: str | Path,
) -> B5OptimizationConfig:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise B5ConfigurationError(f"B5 config not found: {source}")
    raw = source.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise B5ConfigurationError("B5 config is not valid TOML") from error

    if _required(data, "schema_version", int) != 1:
        raise B5ConfigurationError("Only B5 schema_version 1 is supported")
    if _required(data, "baseline", str) != "B5":
        raise B5ConfigurationError("DSPY-001 config must define baseline B5")
    if _required(data, "split", str) != "development":
        raise B5ConfigurationError("DSPY-001 may use only development")
    if _required(data, "optimizer", str) != "MIPROv2":
        raise B5ConfigurationError("DSPY-001 requires MIPROv2")
    optimizer_auto = _required(data, "optimizer_auto", str)
    if optimizer_auto != "none":
        raise B5ConfigurationError(
            "B5 requires an explicit budget; optimizer_auto must be 'none'"
        )

    base_path_value = _required(data, "base_experiment_config", str)
    base_path = (source.parent / base_path_value).resolve()
    expected_base_hash = _required(data, "base_experiment_config_sha256", str)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_base_hash):
        raise B5ConfigurationError(
            "base_experiment_config_sha256 must be a lowercase SHA-256"
        )
    if not base_path.is_file():
        raise B5ConfigurationError(f"Frozen B4 config not found: {base_path}")
    actual_base_hash = hashlib.sha256(base_path.read_bytes()).hexdigest()
    if actual_base_hash != expected_base_hash:
        raise B5ConfigurationError(
            "Frozen B4 config checksum does not match DSPY-001"
        )
    try:
        base_config = load_baseline_config(base_path)
    except ExperimentConfigurationError as error:
        raise B5ConfigurationError(f"Invalid frozen B4 config: {error}") from error
    if base_config.baseline != "B4":
        raise B5ConfigurationError("DSPY-001 must build on frozen B4")

    positive_fields = (
        "num_candidates",
        "num_trials",
        "num_threads",
        "max_errors",
    )
    positive = {key: _required(data, key, int) for key in positive_fields}
    if any(value <= 0 for value in positive.values()):
        raise B5ConfigurationError(
            "B5 candidate, trial, thread, and error limits must be positive"
        )
    max_bootstrapped = _required(data, "max_bootstrapped_demos", int)
    max_labeled = _required(data, "max_labeled_demos", int)
    if max_bootstrapped < 0 or max_labeled < 0:
        raise B5ConfigurationError("B5 demonstration limits cannot be negative")
    if max_labeled != 0:
        raise B5ConfigurationError(
            "B5 must not use labeled demos because Spider2 gold SQL is prohibited"
        )
    if _required(data, "minibatch", bool):
        raise B5ConfigurationError(
            "The frozen small-development B5 policy requires minibatch=false"
        )

    tokens_per_minute = _required(data, "tokens_per_minute", int)
    token_safety_margin = _number(data, "token_safety_margin")
    rate_limit_window_seconds = _number(data, "rate_limit_window_seconds")
    rate_limit_buffer_seconds = _number(data, "rate_limit_buffer_seconds")
    rate_limit_max_retries = _required(data, "rate_limit_max_retries", int)
    cache = _required(data, "cache", bool)
    cache_schema_version = _required(data, "cache_schema_version", int)
    cache_size_limit_bytes = _required(data, "cache_size_limit_bytes", int)
    cache_resume_max_age_hours = _required(
        data, "cache_resume_max_age_hours", int
    )
    if tokens_per_minute <= 0:
        raise B5ConfigurationError("tokens_per_minute must be positive")
    if not 0 < token_safety_margin <= 1:
        raise B5ConfigurationError("token_safety_margin must be in (0, 1]")
    if rate_limit_window_seconds <= 0 or rate_limit_buffer_seconds < 0:
        raise B5ConfigurationError("B5 rate-limit timing values are invalid")
    if rate_limit_max_retries < 0:
        raise B5ConfigurationError("rate_limit_max_retries cannot be negative")
    if cache_schema_version != 1:
        raise B5ConfigurationError("Only B5 cache_schema_version 1 is supported")
    if cache_size_limit_bytes <= 0 or cache_resume_max_age_hours <= 0:
        raise B5ConfigurationError(
            "B5 cache size and resume age limits must be positive"
        )

    program_aware_proposer = _required(data, "program_aware_proposer", bool)
    data_aware_proposer = _required(data, "data_aware_proposer", bool)
    tip_aware_proposer = _required(data, "tip_aware_proposer", bool)
    fewshot_aware_proposer = _required(
        data, "fewshot_aware_proposer", bool
    )
    if data_aware_proposer or fewshot_aware_proposer:
        raise B5ConfigurationError(
            "B5 disables data/few-shot-aware proposers because full B4 "
            "contexts cannot be concatenated within the frozen TPM budget"
        )

    train_databases = _string_tuple(data, "train_database_ids")
    validation_databases = _string_tuple(data, "validation_database_ids")
    overlap = sorted(set(train_databases) & set(validation_databases))
    if overlap:
        raise B5ConfigurationError(
            f"B5 train/validation databases overlap: {overlap}"
        )

    return B5OptimizationConfig(
        schema_version=1,
        optimization_id=_required(data, "optimization_id", str),
        baseline="B5",
        split="development",
        program_version=_required(data, "program_version", str),
        dspy_version=_required(data, "dspy_version", str),
        litellm_version=_required(data, "litellm_version", str),
        optuna_version=_required(data, "optuna_version", str),
        optimizer="MIPROv2",
        optimizer_auto=optimizer_auto,
        num_candidates=positive["num_candidates"],
        num_trials=positive["num_trials"],
        max_bootstrapped_demos=max_bootstrapped,
        max_labeled_demos=max_labeled,
        minibatch=False,
        num_threads=positive["num_threads"],
        max_errors=positive["max_errors"],
        optimizer_seed=_required(data, "optimizer_seed", int),
        dspy_model_id=_required(data, "dspy_model_id", str),
        cache=cache,
        cache_schema_version=cache_schema_version,
        cache_size_limit_bytes=cache_size_limit_bytes,
        cache_resume_max_age_hours=cache_resume_max_age_hours,
        tokens_per_minute=tokens_per_minute,
        token_safety_margin=token_safety_margin,
        rate_limit_window_seconds=rate_limit_window_seconds,
        rate_limit_buffer_seconds=rate_limit_buffer_seconds,
        rate_limit_max_retries=rate_limit_max_retries,
        program_aware_proposer=program_aware_proposer,
        data_aware_proposer=data_aware_proposer,
        tip_aware_proposer=tip_aware_proposer,
        fewshot_aware_proposer=fewshot_aware_proposer,
        train_database_ids=train_databases,
        validation_database_ids=validation_databases,
        base_config=base_config,
        base_config_sha256=actual_base_hash,
        config_path=source,
        config_sha256=hashlib.sha256(raw).hexdigest(),
    )
