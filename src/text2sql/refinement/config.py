from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from text2sql.execution import SandboxPolicy
from text2sql.experiments.config import BaselineExperimentConfig, load_baseline_config


@dataclass(frozen=True)
class RefinementConfig:
    experiment_id: str
    base: BaselineExperimentConfig
    config_sha256: str
    sandbox: SandboxPolicy
    max_candidates: int = 3
    max_repairs: int = 1
    max_input_tokens: int = 6000
    max_calls: int = 124
    request_spacing_seconds: float = 62.0
    prompt_version: str = "ref001-b6r-strategies-v1"
    ranker_version: str = "ref001-deterministic-v1"

    def __post_init__(self) -> None:
        if self.base.baseline != "B6R" or self.base.split != "development":
            raise ValueError("REF-001 requires a frozen development B6R base")
        for name, value, upper in (("max_candidates", self.max_candidates, 3),
                                   ("max_repairs", self.max_repairs, 1),
                                   ("max_calls", self.max_calls, 124),
                                   ("max_input_tokens", self.max_input_tokens, 6000)):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(f"Invalid {name}")
        if self.request_spacing_seconds != 62.0:
            raise ValueError("REF-001 freezes one provider request per 62 seconds")
        if self.prompt_version != "ref001-b6r-strategies-v1" or self.ranker_version != "ref001-deterministic-v1":
            raise ValueError("Unsupported REF-001 prompt or ranker version")
        if self.base.max_tokens + self.max_input_tokens > 7200:
            raise ValueError("A B7 request must fit the frozen 7200-token safe budget")


def load_refinement_config(path: str | Path) -> RefinementConfig:
    path = Path(path).resolve()
    raw = path.read_bytes()
    data = tomllib.loads(raw.decode())
    expected = {"schema_version", "experiment_id", "split", "base_config", "base_config_sha256",
                "max_candidates", "max_repairs", "max_input_tokens", "max_calls",
                "request_spacing_seconds", "prompt_version", "ranker_version", "sandbox"}
    if set(data) != expected or data["schema_version"] != 1 or data["split"] != "development":
        raise ValueError("Invalid REF-001 configuration or split")
    base = load_baseline_config(path.parent / data["base_config"])
    if base.config_sha256 != data["base_config_sha256"]:
        raise ValueError("REF-001 B6R checksum mismatch")
    if not isinstance(data["experiment_id"], str) or not data["experiment_id"].strip():
        raise ValueError("experiment_id must not be empty")
    sandbox = SandboxPolicy(**data["sandbox"])
    return RefinementConfig(
        experiment_id=data["experiment_id"], base=base,
        config_sha256=hashlib.sha256(raw).hexdigest(), sandbox=sandbox,
        **{key: data[key] for key in ("max_candidates", "max_repairs", "max_input_tokens",
                                     "max_calls", "request_spacing_seconds", "prompt_version", "ranker_version")},
    )
