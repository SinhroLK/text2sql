from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import dspy
import litellm
from dspy.utils.exceptions import LMRateLimitError

from text2sql.optimization import (
    B5RecoveryError,
    B5RecoverySession,
    StrictRunCache,
    TokenAwareDSPyLM,
    TokenAwareLMError,
    TokenBudgetPolicy,
)


IDENTITY = {
    "schema_version": 1,
    "optimization_id": "fixture-b5",
    "config_sha256": "a" * 64,
}
IDENTITY_SHA256 = "b" * 64


def _response(
    content: str = "SELECT 1",
    tokens: int = 7,
    finish_reason: str = "stop",
) -> Any:
    return litellm.ModelResponse(
        model="openai/gpt-oss-120b",
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        usage={
            "prompt_tokens": max(0, tokens - 2),
            "completion_tokens": 2,
            "total_tokens": tokens,
        },
    )


class _CachingLM(TokenAwareDSPyLM):
    def __init__(
        self,
        identity_sha256: str,
        *,
        fail: bool = False,
        finish_reason: str = "stop",
    ) -> None:
        super().__init__(
            "groq/openai/gpt-oss-120b",
            token_budget_policy=TokenBudgetPolicy(1000, 0.9, 60.0, 2.0, 0),
            provider_error_retries=0,
            recovery_identity_sha256=identity_sha256,
            cache=True,
            api_key="fixture-secret",
            max_tokens=10,
            temperature=0.0,
        )
        self.provider_calls = 0
        self.fail = fail
        self.finish_reason = finish_reason

    def _forward_once(
        self,
        prompt: str | None,
        messages: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> Any:
        del prompt, messages, kwargs
        self.provider_calls += 1
        if self.fail:
            raise LMRateLimitError("rate limited", retry_after=0.0)
        return _response(finish_reason=self.finish_reason)


class StrictRunCacheTest(unittest.TestCase):
    def _cache(self, root: Path, identity: str = IDENTITY_SHA256) -> StrictRunCache:
        return StrictRunCache(
            root,
            run_identity_sha256=identity,
            size_limit_bytes=10_000_000,
            forbidden_values=("fixture-secret",),
        )

    def test_cache_hit_skips_provider_and_token_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_cache = dspy.cache
            cache = self._cache(Path(directory))
            dspy.cache = cache
            try:
                lm = _CachingLM(IDENTITY_SHA256)
                first = lm.forward(prompt="Return SQL")
                second = lm.forward(prompt="Return SQL")
            finally:
                dspy.cache = old_cache

            self.assertEqual(first.choices[0].message.content, "SELECT 1")
            self.assertEqual(second.choices[0].message.content, "SELECT 1")
            self.assertEqual(lm.provider_calls, 1)
            snapshot = lm.rate_limit_snapshot()
            self.assertEqual(snapshot["provider_attempts"], 1)
            self.assertEqual(snapshot["cache_hits"], 1)
            self.assertEqual(snapshot["cache_misses"], 1)
            self.assertEqual(snapshot["cache_replayed_provider_tokens"], 7)
            self.assertEqual(cache.stats()["cache_entries"], 1)

    def test_rollout_id_keeps_intentionally_distinct_samples_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_cache = dspy.cache
            cache = self._cache(Path(directory))
            dspy.cache = cache
            try:
                lm = _CachingLM(IDENTITY_SHA256)
                lm.forward(prompt="same", rollout_id="sample-a", temperature=1.0)
                lm.forward(prompt="same", rollout_id="sample-b", temperature=1.0)
                lm.forward(prompt="same", rollout_id="sample-a", temperature=1.0)
            finally:
                dspy.cache = old_cache

            self.assertEqual(lm.provider_calls, 2)
            self.assertEqual(lm.rate_limit_snapshot()["cache_hits"], 1)

    def test_provider_failure_is_never_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_cache = dspy.cache
            cache = self._cache(Path(directory))
            dspy.cache = cache
            try:
                lm = _CachingLM(IDENTITY_SHA256, fail=True)
                with self.assertRaisesRegex(TokenAwareLMError, "0 retries"):
                    lm.forward(prompt="Return SQL")
            finally:
                dspy.cache = old_cache

            self.assertEqual(cache.stats()["cache_entries"], 0)
            self.assertEqual(cache.stats()["cache_writes_this_invocation"], 0)

    def test_truncated_response_is_never_scored_or_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_cache = dspy.cache
            cache = self._cache(Path(directory))
            dspy.cache = cache
            try:
                lm = _CachingLM(IDENTITY_SHA256, finish_reason="length")
                with self.assertRaisesRegex(
                    TokenAwareLMError, "finish_reason='length'"
                ):
                    lm.forward(prompt="Return SQL")
            finally:
                dspy.cache = old_cache

            self.assertEqual(lm.provider_calls, 1)
            self.assertEqual(lm.rate_limit_snapshot()["observed_tokens_total"], 7)
            self.assertEqual(cache.stats()["cache_entries"], 0)
    def test_wrong_active_cache_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_cache = dspy.cache
            cache = self._cache(Path(directory), identity="c" * 64)
            dspy.cache = cache
            try:
                lm = _CachingLM(IDENTITY_SHA256)
                with self.assertRaisesRegex(TokenAwareLMError, "different run"):
                    lm.forward(prompt="Return SQL")
            finally:
                dspy.cache = old_cache

            self.assertEqual(lm.provider_calls, 0)

    def test_secret_in_response_is_rejected_before_cache_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = self._cache(Path(directory))
            with self.assertRaisesRegex(B5RecoveryError, "API key"):
                cache.put(
                    {"prompt": "fixture"},
                    _response("fixture-secret"),
                )
            self.assertEqual(cache.stats()["cache_entries"], 0)

    def test_tampered_ledger_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = self._cache(root)
            request = {"prompt": "fixture"}
            cache.put(request, _response())
            cache.disk_cache.close()
            ledger_path = root / "cache-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            next(iter(ledger["entries"].values()))["response_sha256"] = "0" * 64
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

            reopened = self._cache(root)
            with self.assertRaisesRegex(B5RecoveryError, "integrity"):
                reopened.get(request)


class RecoverySessionTest(unittest.TestCase):
    def _open(
        self,
        root: Path,
        *,
        identity: dict[str, Any] = IDENTITY,
        resume_run_id: str | None = None,
        now: datetime | None = None,
    ) -> B5RecoverySession:
        return B5RecoverySession.open(
            root,
            identity=identity,
            cache_size_limit_bytes=10_000_000,
            resume_max_age_hours=72,
            resume_run_id=resume_run_id,
            now=now,
        )

    def test_fresh_runs_never_share_a_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._open(root)
            second = self._open(root)

            self.assertNotEqual(first.run_id, second.run_id)
            self.assertNotEqual(first.run_dir, second.run_dir)
            first.close()
            second.close()

    def test_resume_requires_exact_identity_and_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started = self._open(root)
            started.cache.put({"prompt": "fixture"}, _response())
            with self.assertRaisesRegex(B5RecoveryError, "already active"):
                self._open(root, resume_run_id=started.run_id)
            started.close()

            resumed = self._open(root, resume_run_id=started.run_id)

            self.assertEqual(resumed.run_id, started.run_id)
            self.assertEqual(resumed.state["resume_count"], 1)
            self.assertIsNotNone(resumed.cache.get({"prompt": "fixture"}))
            with self.assertRaisesRegex(B5RecoveryError, "identity mismatch"):
                self._open(
                    root,
                    identity={**IDENTITY, "config_sha256": "d" * 64},
                    resume_run_id=started.run_id,
                )

            resumed.close()

    def test_stale_completed_and_path_traversal_resumes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = datetime(2026, 9, 3, tzinfo=timezone.utc)
            started = self._open(root, now=created)
            with self.assertRaisesRegex(B5RecoveryError, "resume window"):
                self._open(
                    root,
                    resume_run_id=started.run_id,
                    now=created + timedelta(hours=73),
                )
            started.mark("completed")
            with self.assertRaisesRegex(B5RecoveryError, "already completed"):
                self._open(root, resume_run_id=started.run_id, now=created)
            with self.assertRaisesRegex(B5RecoveryError, "run ID"):
                self._open(root, resume_run_id="../outside", now=created)

            started.close()

    def test_metric_progress_contains_hashes_but_not_generated_sql(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = self._open(Path(directory))
            session.record_metric(
                {
                    "example_id": "local001",
                    "score": 1.0,
                    "status": "correct",
                    "sql_sha256": "e" * 64,
                    "bootstrap_trace": False,
                }
            )
            record = json.loads(
                (session.run_dir / "metric-progress.jsonl").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(record["identity_sha256"], session.identity_sha256)
            self.assertNotIn("generated_sql", record)
            self.assertNotIn("sql", record)

            session.close()


if __name__ == "__main__":
    unittest.main()
