from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from text2sql.domain import GenerationInput


@dataclass(frozen=True)
class ProviderResponse:
    candidates: tuple[str, ...]
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class SQLProvider(Protocol):
    provider_name: str
    model_id: str

    def generate(self, generation_input: GenerationInput) -> ProviderResponse:
        """Generate one or more SQL candidates without executing them."""

