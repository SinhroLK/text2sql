from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from text2sql.development.controls import DeferredRequest, Limits, RequestJournal
from text2sql.development.offline import budget_preflight
from text2sql.providers import GroqProviderError
from text2sql.providers.base import ProviderResponse


class RecoveryControlsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "requests.jsonl"
        self.identity = {"expected_ids": ["a", "b"], "source_sha256": "fixture"}
        self.limits = Limits(4, 1000, 200, 1000, 2, "1.00", "1.00", "2.00")
        self.now = 100.0
        self.calls = 0

    def journal(self, **kwargs):
        return RequestJournal(self.path, self.identity, kwargs.get("limits", self.limits),
                              clock=lambda: self.now)

    def good(self):
        self.calls += 1
        return ProviderResponse(("SELECT 1",), 20, 10,
                                {"completion_complete": True, "finish_reason": "stop"})

    def call(self, journal, key="a", invoke=None, inputs=50, outputs=50, prompt="fixture"):
        return journal.call(key, prompt, input_tokens=inputs, max_completion_tokens=outputs,
                            invoke=invoke or self.good)

    def test_success_replays_without_call_and_changed_prompt_rejected(self):
        with self.journal() as journal:
            result = self.call(journal)
        with self.journal() as journal:
            self.assertEqual(result, self.call(journal))
            with self.assertRaisesRegex(ValueError, "changed"):
                self.call(journal, prompt="changed")
        self.assertEqual(self.calls, 1)

    def test_crash_after_reservation_is_not_reissued(self):
        def crash():
            raise KeyboardInterrupt()
        with self.journal() as journal, self.assertRaises(KeyboardInterrupt):
            self.call(journal, invoke=crash)
        with self.journal() as journal:
            self.assertEqual(self.call(journal)["status"], "interrupted")
        self.assertEqual(self.calls, 0)

    def test_unknown_failure_and_5xx_are_not_retried(self):
        for error in (TimeoutError(), GroqProviderError("hidden", status_code=503)):
            with self.subTest(error=error):
                self.path.unlink(missing_ok=True)
                def fail():
                    self.calls += 1
                    raise error
                before = self.calls
                with self.journal() as journal:
                    self.assertEqual(self.call(journal, invoke=fail)["status"], "failed")
                    self.call(journal, invoke=fail)
                self.assertEqual(self.calls, before + 1)

    def test_explicit_rate_limit_reserves_again_and_obeys_delay(self):
        def reject():
            raise GroqProviderError("rate limited", status_code=429, retry_after_seconds=70)
        with self.journal() as journal:
            self.assertEqual(self.call(journal, invoke=reject)["status"], "known_rejected")
            with self.assertRaises(DeferredRequest) as error:
                self.call(journal)
            self.assertEqual(error.exception.seconds, 70)
            self.now += 71
            self.assertEqual(self.call(journal)["status"], "complete")
            self.assertEqual(len([x for x in journal.events if x["event"] == "reserve"]), 2)

    def test_repeated_rejections_stop_at_attempt_cap(self):
        def reject():
            raise GroqProviderError("rate limited", status_code=429)
        with self.journal() as journal:
            self.call(journal, invoke=reject)
            self.now += 63
            self.call(journal, invoke=reject)
            self.now += 63
            self.assertEqual(self.call(journal)["error_code"], "retry_limit")
            self.assertEqual(self.calls, 0)

    def test_oversized_scope_and_invalid_counts_never_invoke(self):
        with self.journal() as journal:
            for args in ({"inputs": 151}, {"key": "test"}, {"inputs": True}, {"outputs": 0}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    self.call(journal, **args)
            self.assertEqual(self.calls, 0)
            self.assertEqual(len(journal.events), 1)

    def test_rolling_quota_defers_without_sleep_or_call(self):
        with self.journal(limits=replace(self.limits, tokens_per_minute=150)) as journal:
            self.call(journal)
            with self.assertRaises(DeferredRequest) as error:
                self.call(journal, key="b")
            self.assertEqual(error.exception.seconds, 62)
            self.assertEqual(self.calls, 1)
            self.now += 62
            self.call(journal, key="b")
            self.assertEqual(self.calls, 2)

    def test_global_call_token_and_cost_caps(self):
        for limits in (replace(self.limits, max_calls=1),
                       replace(self.limits, max_total_tokens=150),
                       replace(self.limits, max_cost_usd="0.0002")):
            with self.subTest(limits=limits):
                self.path.unlink(missing_ok=True)
                with self.journal(limits=limits) as journal:
                    self.call(journal)
                    with self.assertRaisesRegex(ValueError, "budget exhausted"):
                        self.call(journal, key="b")

    def test_daily_quota_settles_reported_usage_after_resume(self):
        limits = replace(self.limits, tokens_per_day=150)
        with self.journal(limits=limits) as journal:
            self.call(journal)  # reserves 100, reports 30
        self.now += 63
        with self.journal(limits=limits) as journal:
            self.call(journal, key="b")  # 30 + 100 fits
        self.assertEqual(self.calls, 2)

    def test_daily_quota_still_defers_when_actual_usage_exhausts_it(self):
        with self.journal(limits=replace(self.limits, tokens_per_day=120)) as journal:
            self.call(journal)
            self.now += 63
            with self.assertRaises(DeferredRequest) as error:
                self.call(journal, key="b")
            self.assertEqual(error.exception.seconds, 86402 - 63)
            self.now += error.exception.seconds
            self.call(journal, key="b")

    def test_daily_quota_retains_unknown_and_rejected_reservations(self):
        def reject():
            raise GroqProviderError("limited", status_code=429)
        def crash():
            raise KeyboardInterrupt()
        def unknown():
            raise TimeoutError()
        def missing_usage():
            return ProviderResponse(("SELECT 1",), 0, 0,
                                    {"completion_complete": True, "finish_reason": "stop"})
        for invoke in (reject, crash, unknown, missing_usage):
            with self.subTest(invoke=invoke.__name__):
                self.path.unlink(missing_ok=True)
                with self.journal(limits=replace(self.limits, tokens_per_day=150)) as journal:
                    try:
                        self.call(journal, invoke=invoke)
                    except KeyboardInterrupt:
                        pass
                    self.now += 63
                    with self.assertRaises(DeferredRequest):
                        self.call(journal, key="b")

    def test_incomplete_response_cannot_become_selected(self):
        with self.journal() as journal:
            result = self.call(journal, invoke=lambda: ProviderResponse(
                ("SELECT unfinished",), 4, 50,
                {"completion_complete": False, "finish_reason": "length"}))
            self.assertEqual(result["candidates"], [])
            self.assertEqual(result["output_tokens"], 50)
            self.assertEqual(self.call(journal), result)

    def test_lock_tampering_and_changed_identity_fail_closed(self):
        with self.journal():
            with self.assertRaises(BlockingIOError):
                self.journal()
        self.identity = {**self.identity, "source_sha256": "changed"}
        with self.assertRaisesRegex(ValueError, "identity"):
            self.journal()
        row = json.loads(self.path.read_text())
        row["data"]["identity"]["run"]["source_sha256"] = "tamper"
        self.path.write_text(json.dumps(row) + "\n")
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.journal()

    def test_budget_preflight_is_offline_and_preserves_oversized_requests(self):
        path = Path(__file__).resolve().parents[1] / "configs/development/baseline-ablation-v1.json"
        plan = json.loads(path.read_text())
        result = budget_preflight([{"example_id": "a", "prompt": "X" * 10000}], plan)
        self.assertEqual(result["request_count"], 3)
        self.assertEqual(result["requests_exceeding_recorded_quota_scenario"], 3)
        self.assertFalse(result["paid_run_ready"])
        self.assertEqual(result["provider_calls"], 0)
        with self.assertRaises(ValueError):
            budget_preflight([], {**plan, "paid_runs_enabled": True})

    def test_observed_usage_over_reservation_stops_later_calls(self):
        with self.journal() as journal:
            self.call(journal, invoke=lambda: ProviderResponse(("SELECT 1",), 101, 1,
                {"finish_reason": "stop", "completion_complete": True}))
            with self.assertRaisesRegex(ValueError, "usage exceeded"):
                self.call(journal, key="b")
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
