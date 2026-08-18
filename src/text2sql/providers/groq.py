from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from text2sql.domain import GenerationInput
from text2sql.providers.base import ProviderResponse

DEFAULT_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqProviderError(RuntimeError):
    """Provider error that never includes credentials."""


Transport = Callable[[str, dict[str, str], bytes, float], bytes]


def _transport(endpoint: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    request = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise GroqProviderError(f"Groq API returned HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError) as error:
        raise GroqProviderError(f"Groq API request failed: {error}") from error


@dataclass(frozen=True)
class GroqProvider:
    model_id: str
    api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    timeout_seconds: float = 60.0
    endpoint: str = DEFAULT_GROQ_ENDPOINT
    transport: Transport = _transport

    provider_name = "groq"

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        if self.max_tokens <= 0 or self.timeout_seconds <= 0:
            raise ValueError("max_tokens and timeout_seconds must be positive")

    def generate(self, generation_input: GenerationInput) -> ProviderResponse:
        api_key = self.api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise GroqProviderError("GROQ_API_KEY is required for the Groq provider")
        payload = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": generation_input.prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "n": 1,
            "stream": False,
        }
        raw = self.transport(
            self.endpoint,
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json.dumps(payload, separators=(",", ":")).encode(),
            self.timeout_seconds,
        )
        response = _decode(raw)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise GroqProviderError("Groq API response contains no choices")
        candidates = tuple(
            message["content"].strip()
            for choice in choices
            if isinstance(choice, dict)
            and isinstance((message := choice.get("message")), dict)
            and isinstance(message.get("content"), str)
            and message["content"].strip()
        )
        if not candidates:
            raise GroqProviderError("Groq API response contains no SQL candidate text")
        usage = response.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return ProviderResponse(
            candidates=candidates,
            input_tokens=_count(usage.get("prompt_tokens")),
            output_tokens=_count(usage.get("completion_tokens")),
        )


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GroqProviderError("Groq API returned invalid JSON") from error
    if not isinstance(value, dict):
        raise GroqProviderError("Groq API returned an unexpected JSON value")
    if isinstance(value.get("error"), dict):
        raise GroqProviderError(f"Groq API error: {value['error'].get('message', 'unknown error')}")
    return value


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
