from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    max_retries: int
    timeout_seconds: float
    config_path: Path
    config_sha256: str


def _required(data: dict[str, Any], key: str, expected: type) -> Any:
    value = data.get(key)
    if not isinstance(value, expected) or isinstance(value, bool) and expected is int:
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
        raise ExperimentConfigurationError("Experiment config is not valid TOML") from error

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
        raise ExperimentConfigurationError("Only experiment schema_version 1 is supported")
    expected_variant = {"B0": "question_only", "B1": "simple_schema"}.get(baseline)
    if expected_variant is None or prompt_variant != expected_variant:
        raise ExperimentConfigurationError("B0/B1 baseline and prompt_variant do not match")
    if split != "development":
        raise ExperimentConfigurationError("EXP-001 may run only on the development split")
    if provider != "groq":
        raise ExperimentConfigurationError("EXP-001 provider must be groq")
    if not experiment_id.strip() or not model_id.strip():
        raise ExperimentConfigurationError("experiment_id and model_id must not be empty")
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
        raise ExperimentConfigurationError("temperature must be numeric")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise ExperimentConfigurationError("timeout_seconds must be numeric")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise ExperimentConfigurationError("seed must be an integer or omitted")

    max_tokens = _required(data, "max_tokens", int)
    if reasoning_effort not in ("low", "medium", "high"):
        raise ExperimentConfigurationError("reasoning_effort must be low, medium, or high")
    max_retries = _required(data, "max_retries", int)
    if max_tokens <= 0 or max_retries < 0 or timeout_seconds <= 0:
        raise ExperimentConfigurationError(
            "max_tokens and timeout_seconds must be positive; max_retries must be non-negative"
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
        max_retries=max_retries,
        timeout_seconds=float(timeout_seconds),
        config_path=source,
        config_sha256=hashlib.sha256(raw).hexdigest(),
        reasoning_effort=reasoning_effort,
    )
