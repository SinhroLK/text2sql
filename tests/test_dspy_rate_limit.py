from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from typing import Any

from dspy.utils.exceptions import LMRateLimitError

from text2sql.optimization import (
    RollingTokenLimiter,
    TokenAwareDSPyLM,
    TokenBudgetExceededError,
    TokenAwareLMError,
    TokenBudgetPolicy,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _RetryLM(TokenAwareDSPyLM):
    def __init__(
        self,
        *,
        limiter: RollingTokenLimiter,
        events: list[dict[str, Any]],
    ) -> None:
        super().__init__(
            "groq/openai/gpt-oss-120b",
            api_key="placeholder",
            cache=False,
            max_tokens=10,
            token_budget_policy=limiter.policy,
            provider_error_retries=0,
            limiter=limiter,
            event_sink=events.append,
        )
        self.calls = 0

    def _forward_once(
        self,
        prompt: str | None,
        messages: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> Any:
        self.calls += 1
        if self.calls == 1:
            raise LMRateLimitError("rate limited", retry_after=5.0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="SELECT 1"),
                )
            ],
            usage=SimpleNamespace(
                total_tokens=20,
                prompt_tokens=12,
                completion_tokens=8,
                model_dump=lambda: {
                    "total_tokens": 20,
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                },
            )
        )


class RollingTokenLimiterTest(unittest.TestCase):
    def _policy(self, **overrides: Any) -> TokenBudgetPolicy:
        values: dict[str, Any] = {
            "tokens_per_minute": 100,
            "safety_margin": 0.9,
            "window_seconds": 60.0,
            "buffer_seconds": 2.0,
            "max_rate_limit_retries": 2,
        }
        values.update(overrides)
        return TokenBudgetPolicy(**values)

    def test_observed_usage_replaces_conservative_reservation(self) -> None:
        clock = _Clock()
        events: list[dict[str, Any]] = []
        limiter = RollingTokenLimiter(
            self._policy(),
            clock=clock.monotonic,
            sleep=clock.sleep,
            event_sink=events.append,
        )

        first = limiter.reserve(60)
        limiter.commit(first, 50)
        limiter.reserve(40)
        limiter.reserve(1)

        self.assertEqual(clock.sleeps, [62.0])
        snapshot = limiter.snapshot()
        self.assertEqual(snapshot["safe_token_budget"], 90)
        self.assertEqual(snapshot["provider_attempts"], 3)
        self.assertEqual(snapshot["estimated_tokens_reserved_total"], 101)
        self.assertEqual(snapshot["observed_tokens_total"], 50)
        self.assertEqual(snapshot["throttle_waits"], 1)
        self.assertEqual(snapshot["throttle_wait_seconds"], 62.0)
        self.assertEqual(events[0]["event"], "dspy_token_budget_wait")
        self.assertEqual(events[0]["active_window_tokens"], 90)
        self.assertEqual(events[0]["requested_tokens"], 1)

    def test_single_request_larger_than_safe_budget_fails_early(self) -> None:
        limiter = RollingTokenLimiter(self._policy())

        with self.assertRaisesRegex(
            TokenBudgetExceededError, "Estimated request size"
        ):
            limiter.reserve(91)

    def test_deep_copies_share_one_process_wide_budget(self) -> None:
        limiter = RollingTokenLimiter(self._policy())

        self.assertIs(copy.deepcopy(limiter), limiter)

    def test_exhausted_rate_limit_has_sanitized_terminal_error(self) -> None:
        clock = _Clock()
        policy = self._policy(
            tokens_per_minute=1000, max_rate_limit_retries=0
        )
        limiter = RollingTokenLimiter(
            policy, clock=clock.monotonic, sleep=clock.sleep
        )
        lm = _RetryLM(limiter=limiter, events=[])

        with self.assertRaisesRegex(
            TokenAwareLMError, "persisted after 0 retries"
        ):
            lm.forward(
                messages=[{"role": "user", "content": "Return SELECT 1"}]
            )

        self.assertEqual(lm.calls, 1)
        self.assertEqual(clock.sleeps, [])

    def test_lm_honors_structured_retry_after_and_records_usage(self) -> None:
        clock = _Clock()
        policy = self._policy(tokens_per_minute=1000)
        limiter = RollingTokenLimiter(
            policy, clock=clock.monotonic, sleep=clock.sleep
        )
        events: list[dict[str, Any]] = []
        lm = _RetryLM(limiter=limiter, events=events)

        response = lm.forward(
            messages=[{"role": "user", "content": "Return SELECT 1"}]
        )

        self.assertEqual(response.usage.total_tokens, 20)
        self.assertEqual(lm.calls, 2)
        self.assertEqual(clock.sleeps, [7.0])
        self.assertEqual(events[0]["event"], "dspy_rate_limit_wait")
        self.assertEqual(events[0]["wait_seconds"], 7.0)
        self.assertNotIn("message", events[0])
        snapshot = lm.rate_limit_snapshot()
        self.assertEqual(snapshot["rate_limit_retries"], 1)
        self.assertEqual(snapshot["rate_limit_wait_seconds"], 7.0)
        self.assertEqual(snapshot["provider_attempts"], 2)
        self.assertEqual(snapshot["observed_tokens_total"], 20)


if __name__ == "__main__":
    unittest.main()
