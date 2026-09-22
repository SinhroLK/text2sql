from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable
import groq

from text2sql.domain import GenerationInput
from text2sql.providers.base import ProviderResponse

DEFAULT_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqProviderError(RuntimeError):
    """Sanitized provider error with machine-readable retry metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        token_reset: str | None = None,
        retryable: bool = False,
        category: str = "provider_error",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.token_reset = token_reset
        self.retryable = retryable
        self.category = category


Transport = Callable[[str, dict[str, str], bytes, float], bytes]


def _duration_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    try:
        numeric = float(stripped)
    except ValueError:
        numeric = None
    if numeric is not None:
        return max(0.0, numeric)
    match = re.fullmatch(
        r"(?:(?P<hours>[0-9]+(?:\.[0-9]+)?)h)?"
        r"(?:(?P<minutes>[0-9]+(?:\.[0-9]+)?)m)?"
        r"(?:(?P<seconds>[0-9]+(?:\.[0-9]+)?)s)?",
        stripped,
    )
    if match is None or not any(match.groupdict().values()):
        return None
    return (
        float(match.group("hours") or 0) * 3600
        + float(match.group("minutes") or 0) * 60
        + float(match.group("seconds") or 0)
    )


def _sanitize_provider_detail(detail: str) -> str:
    sanitized = re.sub(
        r"\b(?:org|project)_[A-Za-z0-9_-]+\b",
        "[redacted-id]",
        detail.strip(),
    )
    return re.sub(
        r"\bgsk_[A-Za-z0-9_-]+\b",
        "[redacted-api-key]",
        sanitized,
    )[:1000]


def _status_error_message(
    status_code: int,
    body: object,
    headers: Mapping[str, str],
) -> str:
    parts = [f"Groq API returned HTTP {status_code}"]
    if isinstance(body, dict):
        error = body.get("error")
        detail = error.get("message") if isinstance(error, dict) else None
        if isinstance(detail, str) and detail.strip():
            parts.append(_sanitize_provider_detail(detail))
    retry_after = headers.get("retry-after")
    reset_tokens = headers.get("x-ratelimit-reset-tokens")
    if retry_after:
        parts.append(f"retry-after={retry_after}")
    if reset_tokens:
        parts.append(f"token-reset={reset_tokens}")
    return ": ".join(parts)


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
        retry_after = _duration_seconds(
            error.response.headers.get("retry-after")
        )
        token_reset = error.response.headers.get("x-ratelimit-reset-tokens")
        token_reset_seconds = _duration_seconds(token_reset)
        waits = [
            item
            for item in (retry_after, token_reset_seconds)
            if item is not None
        ]
        status_code = error.status_code
        raise GroqProviderError(
            _status_error_message(
                status_code,
                error.body,
                error.response.headers,
            ),
            status_code=status_code,
            retry_after_seconds=max(waits) if waits else None,
            token_reset=token_reset,
            retryable=(
                status_code == 429
                or status_code in {408, 409, 425}
                or status_code >= 500
            ),
            category=(
                "rate_limit"
                if status_code == 429
                else "provider_unavailable"
                if status_code >= 500
                else "request_rejected"
            ),
        ) from error
    except groq.APIConnectionError as error:
        raise GroqProviderError(
            "Groq API request failed",
            retryable=True,
            category="connection_error",
        ) from error
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
            except GroqProviderError as error:
                if attempt == self.max_retries or not error.retryable:
                    raise
                self.sleep(
                    error.retry_after_seconds
                    if error.retry_after_seconds is not None
                    else 2 ** attempt
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
        detail = value["error"].get("message")
        message = (
            _sanitize_provider_detail(detail)
            if isinstance(detail, str) and detail.strip()
            else "unknown error"
        )
        raise GroqProviderError(f"Groq API error: {message}")
    return value


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
