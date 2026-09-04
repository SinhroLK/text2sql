from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import dspy
import litellm
from dspy.utils.exceptions import LMError, LMProviderError, LMRateLimitError


class TokenBudgetExceededError(LMError):
    """A single request cannot fit inside the configured safe TPM budget."""


class TokenAwareLMError(LMError):
    """Sanitized terminal error from the token-aware provider boundary."""


@dataclass(frozen=True)
class TokenBudgetPolicy:
    tokens_per_minute: int
    safety_margin: float
    window_seconds: float
    buffer_seconds: float
    max_rate_limit_retries: int

    def __post_init__(self) -> None:
        if self.tokens_per_minute <= 0:
            raise ValueError("tokens_per_minute must be positive")
        if not 0 < self.safety_margin <= 1:
            raise ValueError("safety_margin must be in (0, 1]")
        if self.window_seconds <= 0 or self.buffer_seconds < 0:
            raise ValueError("rate-limit timing values are invalid")
        if self.max_rate_limit_retries < 0:
            raise ValueError("max_rate_limit_retries must not be negative")

    @property
    def safe_token_budget(self) -> int:
        return max(1, int(self.tokens_per_minute * self.safety_margin))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens_per_minute": self.tokens_per_minute,
            "safety_margin": self.safety_margin,
            "safe_token_budget": self.safe_token_budget,
            "window_seconds": self.window_seconds,
            "buffer_seconds": self.buffer_seconds,
            "max_rate_limit_retries": self.max_rate_limit_retries,
        }


@dataclass
class _Reservation:
    created_at: float
    tokens: int


class RollingTokenLimiter:
    """Thread-safe rolling-window token reservations shared by all DSPy calls."""

    def __init__(
        self,
        policy: TokenBudgetPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.policy = policy
        self._clock = clock
        self._sleep = sleep
        self._event_sink = event_sink
        self._lock = threading.Lock()
        self._order: deque[int] = deque()
        self._reservations: dict[int, _Reservation] = {}
        self._next_id = 1
        self._requests = 0
        self._estimated_tokens = 0
        self._observed_tokens = 0
        self._throttle_waits = 0
        self._throttle_wait_seconds = 0.0
        self._rate_limit_retries = 0
        self._rate_limit_wait_seconds = 0.0
        self._provider_retries = 0
        self._provider_retry_wait_seconds = 0.0
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_replayed_provider_tokens = 0

    def __deepcopy__(self, memo: dict[int, object]) -> "RollingTokenLimiter":
        memo[id(self)] = self
        return self

    def _purge(self, now: float) -> None:
        cutoff = now - self.policy.window_seconds
        while self._order:
            reservation_id = self._order[0]
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                self._order.popleft()
                continue
            if reservation.created_at > cutoff:
                break
            self._order.popleft()
            self._reservations.pop(reservation_id, None)

    def reserve(self, tokens: int) -> int:
        if tokens <= 0:
            raise ValueError("reserved tokens must be positive")
        if tokens > self.policy.safe_token_budget:
            raise TokenBudgetExceededError(
                f"Estimated request size {tokens} exceeds the safe rolling TPM "
                f"budget {self.policy.safe_token_budget}; use a higher Groq "
                "tier or a separately versioned smaller prompt/output policy"
            )

        while True:
            with self._lock:
                now = self._clock()
                self._purge(now)
                used = sum(item.tokens for item in self._reservations.values())
                if used + tokens <= self.policy.safe_token_budget:
                    reservation_id = self._next_id
                    self._next_id += 1
                    self._reservations[reservation_id] = _Reservation(now, tokens)
                    self._order.append(reservation_id)
                    self._requests += 1
                    self._estimated_tokens += tokens
                    return reservation_id
                oldest = self._reservations[self._order[0]]
                wait_seconds = max(
                    0.0,
                    oldest.created_at
                    + self.policy.window_seconds
                    - now
                    + self.policy.buffer_seconds,
                )
                self._throttle_waits += 1
                self._throttle_wait_seconds += wait_seconds
                event = {
                    "event": "dspy_token_budget_wait",
                    "active_window_tokens": used,
                    "requested_tokens": tokens,
                    "safe_token_budget": self.policy.safe_token_budget,
                    "wait_seconds": round(wait_seconds, 3),
                }
            if self._event_sink is not None:
                self._event_sink(event)
            self._sleep(wait_seconds)

    def commit(self, reservation_id: int, observed_tokens: int | None) -> None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                return
            if observed_tokens is not None and observed_tokens > 0:
                reservation.tokens = observed_tokens
                self._observed_tokens += observed_tokens

    def release(self, reservation_id: int) -> None:
        with self._lock:
            self._reservations.pop(reservation_id, None)

    def wait_after_rate_limit(self, seconds: float) -> None:
        wait_seconds = max(0.0, seconds) + self.policy.buffer_seconds
        with self._lock:
            self._rate_limit_retries += 1
            self._rate_limit_wait_seconds += wait_seconds
        self._sleep(wait_seconds)

    def wait_after_provider_error(self, seconds: float) -> None:
        wait_seconds = max(0.0, seconds)
        with self._lock:
            self._provider_retries += 1
            self._provider_retry_wait_seconds += wait_seconds
        self._sleep(wait_seconds)

    def record_cache_hit(self, original_provider_tokens: int) -> None:
        with self._lock:
            self._cache_hits += 1
            if original_provider_tokens > 0:
                self._cache_replayed_provider_tokens += original_provider_tokens

    def record_cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            self._purge(now)
            active_tokens = sum(
                item.tokens for item in self._reservations.values()
            )
            return {
                **self.policy.to_dict(),
                "provider_attempts": self._requests,
                "estimated_tokens_reserved_total": self._estimated_tokens,
                "observed_tokens_total": self._observed_tokens,
                "active_window_tokens": active_tokens,
                "throttle_waits": self._throttle_waits,
                "throttle_wait_seconds": round(self._throttle_wait_seconds, 3),
                "rate_limit_retries": self._rate_limit_retries,
                "rate_limit_wait_seconds": round(
                    self._rate_limit_wait_seconds, 3
                ),
                "provider_retries": self._provider_retries,
                "provider_retry_wait_seconds": round(
                    self._provider_retry_wait_seconds, 3
                ),
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "cache_replayed_provider_tokens": (
                    self._cache_replayed_provider_tokens
                ),
            }


def _usage_dict(response: Any) -> dict[str, Any] | None:
    usage = response.get("usage") if isinstance(response, Mapping) else getattr(
        response, "usage", None
    )
    if isinstance(usage, Mapping):
        return dict(usage)
    model_dump = getattr(usage, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dict(dumped) if isinstance(dumped, Mapping) else None
    return None


def _observed_tokens(response: Any) -> int | None:
    usage = _usage_dict(response)
    if not usage:
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        return total
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    if all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (prompt, completion)
    ):
        combined = prompt + completion
        return combined or None
    return None

def _require_complete_response(response: Any) -> None:
    choices = (
        response.get("choices")
        if isinstance(response, Mapping)
        else getattr(response, "choices", None)
    )
    if not isinstance(choices, list) or not choices:
        raise TokenAwareLMError(
            "Provider returned no completion; the response was not cached or scored"
        )
    for choice in choices:
        finish_reason = (
            choice.get("finish_reason")
            if isinstance(choice, Mapping)
            else getattr(choice, "finish_reason", None)
        )
        if finish_reason != "stop":
            label = finish_reason if isinstance(finish_reason, str) else "missing"
            raise TokenAwareLMError(
                f"Provider completion ended with finish_reason={label!r}; "
                "the incomplete response was not cached or scored"
            )
        message = (
            choice.get("message")
            if isinstance(choice, Mapping)
            else getattr(choice, "message", None)
        )
        content = (
            message.get("content")
            if isinstance(message, Mapping)
            else getattr(message, "content", None)
        )
        if not isinstance(content, str) or not content.strip():
            raise TokenAwareLMError(
                "Provider returned an empty completion; "
                "the response was not cached or scored"
            )


_RETRY_SECONDS = re.compile(
    r"(?:try again in|retry-after[=:]?)\s*([0-9]+(?:\.[0-9]+)?)\s*s",
    re.IGNORECASE,
)


def _retry_after(error: LMRateLimitError, fallback: float) -> float:
    structured = getattr(error, "retry_after", None)
    if isinstance(structured, (int, float)) and structured >= 0:
        return float(structured)
    match = _RETRY_SECONDS.search(str(error))
    return float(match.group(1)) if match else fallback


class TokenAwareDSPyLM(dspy.LM):
    """DSPy LM with scoped recovery caching and token reservations."""

    def __init__(
        self,
        model: str,
        *,
        token_budget_policy: TokenBudgetPolicy,
        provider_error_retries: int,
        recovery_identity_sha256: str | None = None,
        limiter: RollingTokenLimiter | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if provider_error_retries < 0:
            raise ValueError("provider_error_retries must not be negative")
        self.token_budget_policy = token_budget_policy
        self.event_sink = event_sink or self._default_event_sink
        self.token_limiter = limiter or RollingTokenLimiter(
            token_budget_policy, event_sink=self.event_sink
        )
        self.provider_error_retries = provider_error_retries
        requested_cache = kwargs.pop("cache", False)
        if not isinstance(requested_cache, bool):
            raise ValueError("cache must be boolean")
        if requested_cache and not recovery_identity_sha256:
            raise ValueError("B5 caching requires a run-scoped recovery identity")
        self.recovery_cache_enabled = requested_cache
        self.recovery_identity_sha256 = recovery_identity_sha256
        # B5 probes its scoped cache before reserving TPM. The parent cache is
        # disabled so a cache hit never waits or appears as provider usage.
        super().__init__(model, num_retries=0, cache=False, **kwargs)

    @staticmethod
    def _default_event_sink(event: dict[str, Any]) -> None:
        print(
            json.dumps(event, ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
            flush=True,
        )

    def _estimate_request_tokens(
        self,
        prompt: str | None,
        messages: list[dict[str, Any]] | None,
        kwargs: dict[str, Any],
    ) -> tuple[int, int]:
        count_messages = messages
        if count_messages is None and prompt is not None:
            count_messages = [{"role": "user", "content": prompt}]
        try:
            input_tokens = litellm.token_counter(
                model=self.model,
                text=prompt if count_messages is None else None,
                messages=count_messages,
            )
        except Exception:
            rendered = prompt or json.dumps(
                count_messages or [], ensure_ascii=False, sort_keys=True
            )
            input_tokens = max(1, (len(rendered) + 3) // 4)
        configured_max = kwargs.get(
            "max_tokens", self.kwargs.get("max_tokens", 0)
        )
        max_output_tokens = (
            configured_max
            if isinstance(configured_max, int)
            and not isinstance(configured_max, bool)
            and configured_max > 0
            else 0
        )
        return input_tokens, input_tokens + max_output_tokens

    def _forward_once(
        self,
        prompt: str | None,
        messages: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> Any:
        call_kwargs = dict(kwargs)
        call_kwargs.pop("cache", None)
        return super().forward(
            prompt=prompt, messages=messages, cache=False, **call_kwargs
        )

    @staticmethod
    def _redact_cache_secrets(value: Any) -> Any:
        if isinstance(value, Mapping):
            sanitized: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = str(raw_key)
                folded = key.casefold().replace("-", "_")
                if any(
                    marker in folded
                    for marker in (
                        "api_key",
                        "authorization",
                        "password",
                        "secret",
                    )
                ):
                    sanitized[key] = "<redacted>"
                else:
                    sanitized[key] = TokenAwareDSPyLM._redact_cache_secrets(item)
            return sanitized
        if isinstance(value, (list, tuple)):
            return [
                TokenAwareDSPyLM._redact_cache_secrets(item) for item in value
            ]
        return value

    def _cache_request(
        self,
        prompt: str | None,
        messages: list[dict[str, Any]] | None,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_messages = messages or [{"role": "user", "content": prompt}]
        merged = {**self.kwargs, **kwargs}
        merged.pop("cache", None)
        return {
            "cache_protocol": "b5-run-scoped-lm-response-v1",
            "run_identity_sha256": self.recovery_identity_sha256,
            "model": self.model,
            "model_type": self.model_type,
            "messages": self._redact_cache_secrets(normalized_messages),
            "settings": self._redact_cache_secrets(merged),
        }

    def _recovery_cache(self) -> Any:
        cache = dspy.cache
        if (
            getattr(cache, "run_identity_sha256", None)
            != self.recovery_identity_sha256
        ):
            raise TokenAwareLMError(
                "B5 recovery cache is missing or belongs to a different run"
            )
        return cache

    def forward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        if (
            "cache" in kwargs
            and bool(kwargs["cache"]) != self.recovery_cache_enabled
        ):
            raise TokenAwareLMError(
                "Per-call cache overrides are prohibited for frozen B5 runs"
            )
        cache_request: dict[str, Any] | None = None
        if self.recovery_cache_enabled:
            cache_request = self._cache_request(prompt, messages, kwargs)
            try:
                cached = self._recovery_cache().get(cache_request)
            except TokenAwareLMError:
                raise
            except Exception as error:
                raise TokenAwareLMError(
                    "B5 recovery cache validation failed"
                ) from error
            if cached is not None:
                original_tokens = (
                    cached.get("b5_cached_provider_tokens", 0)
                    if isinstance(cached, Mapping)
                    else getattr(cached, "b5_cached_provider_tokens", 0)
                )
                self.token_limiter.record_cache_hit(
                    original_tokens if isinstance(original_tokens, int) else 0
                )
                self.event_sink(
                    {
                        "event": "dspy_recovery_cache_hit",
                        "original_provider_tokens": original_tokens,
                    }
                )
                return cached
            self.token_limiter.record_cache_miss()
        input_tokens, reserved_tokens = self._estimate_request_tokens(
            prompt, messages, kwargs
        )
        rate_attempt = 0
        provider_attempt = 0
        while True:
            reservation_id = self.token_limiter.reserve(reserved_tokens)
            try:
                response = self._forward_once(prompt, messages, **kwargs)
            except LMRateLimitError as error:
                self.token_limiter.release(reservation_id)
                if rate_attempt >= self.token_budget_policy.max_rate_limit_retries:
                    raise TokenAwareLMError(
                        "Groq TPM rate limit persisted after "
                        f"{rate_attempt} retries; wait for quota reset or "
                        "increase the provider tier"
                    ) from None
                rate_attempt += 1
                wait_seconds = _retry_after(
                    error, self.token_budget_policy.window_seconds
                )
                self.event_sink(
                    {
                        "event": "dspy_rate_limit_wait",
                        "attempt": rate_attempt,
                        "estimated_input_tokens": input_tokens,
                        "reserved_tokens": reserved_tokens,
                        "wait_seconds": round(
                            wait_seconds + self.token_budget_policy.buffer_seconds,
                            3,
                        ),
                    }
                )
                self.token_limiter.wait_after_rate_limit(wait_seconds)
                continue
            except LMProviderError:
                self.token_limiter.release(reservation_id)
                if provider_attempt >= self.provider_error_retries:
                    raise TokenAwareLMError(
                        "DSPy provider request failed after "
                        f"{provider_attempt} retries"
                    ) from None
                wait_seconds = float(2**provider_attempt)
                provider_attempt += 1
                self.event_sink(
                    {
                        "event": "dspy_provider_retry",
                        "attempt": provider_attempt,
                        "wait_seconds": wait_seconds,
                    }
                )
                self.token_limiter.wait_after_provider_error(wait_seconds)
                continue
            except Exception:
                self.token_limiter.release(reservation_id)
                raise
            observed_tokens = _observed_tokens(response)
            self.token_limiter.commit(reservation_id, observed_tokens)
            _require_complete_response(response)
            if cache_request is not None:
                try:
                    self._recovery_cache().put(cache_request, response)
                except TokenAwareLMError:
                    raise
                except Exception as error:
                    raise TokenAwareLMError(
                        "B5 recovery cache commit failed"
                    ) from error
            return response

    def rate_limit_snapshot(self) -> dict[str, Any]:
        return self.token_limiter.snapshot()
