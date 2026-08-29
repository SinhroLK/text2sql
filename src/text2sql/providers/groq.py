from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable
import groq

from text2sql.domain import GenerationInput
from text2sql.providers.base import ProviderResponse

DEFAULT_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqProviderError(RuntimeError):
    """Provider error that never includes credentials."""


Transport = Callable[[str, dict[str, str], bytes, float], bytes]


def _transport(endpoint: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    authorization = headers.get("Authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix) or not authorization[len(prefix):]:
        raise GroqProviderError("Groq API authorization header is missing")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GroqProviderError("Groq API request contains invalid JSON") from error

    suffix = "/openai/v1/chat/completions"
    if not endpoint.endswith(suffix):
        raise GroqProviderError("Groq chat-completions endpoint is invalid")
    client = groq.Groq(
        api_key=authorization[len(prefix):],
        base_url=endpoint[: -len(suffix)],
        timeout=timeout,
        max_retries=0,
    )
    try:
        response = client.chat.completions.create(**payload)
    except groq.APIStatusError as error:
        raise GroqProviderError(
            f"Groq API returned HTTP {error.status_code}"
        ) from error
    except groq.APIConnectionError as error:
        raise GroqProviderError("Groq API request failed") from error
    except groq.APIError as error:
        raise GroqProviderError(
            f"Groq API request failed: {type(error).__name__}"
        ) from error
    return response.model_dump_json().encode("utf-8")


@dataclass(frozen=True)
class GroqProvider:
    model_id: str
    api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    seed: int | None = None
    reasoning_effort: str | None = None
    timeout_seconds: float = 60.0
    endpoint: str = DEFAULT_GROQ_ENDPOINT
    transport: Transport = _transport
    max_retries: int = 2
    sleep: Callable[[float], None] = time.sleep

    provider_name = "groq"

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        if self.max_tokens <= 0 or self.timeout_seconds <= 0:
            raise ValueError("max_tokens and timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.seed is not None and (not isinstance(self.seed, int) or isinstance(self.seed, bool)):
            raise ValueError("seed must be an integer or None")
        if self.reasoning_effort not in (None, "low", "medium", "high"):
            raise ValueError("reasoning_effort must be low, medium, high, or None")

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
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        for attempt in range(self.max_retries + 1):
            try:
                raw = self.transport(
                    self.endpoint,
                    {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json.dumps(payload, separators=(",", ":")).encode(),
                    self.timeout_seconds,
                )
                break
            except GroqProviderError:
                if attempt == self.max_retries:
                    raise
                self.sleep(2 ** attempt)
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
            metadata={
                "provider_request_id": response.get("id"),
                "provider_model": response.get("model"),
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "seed": self.seed,
                "reasoning_effort": self.reasoning_effort,
                "timeout_seconds": self.timeout_seconds,
                "endpoint": self.endpoint,
            },
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
