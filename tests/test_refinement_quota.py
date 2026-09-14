import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import groq
import httpx

from text2sql.refinement.quota import TokenQuota
from text2sql.refinement.provider import B7GroqProvider
from text2sql.refinement.config import load_refinement_config


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "quota.jsonl"
        self.now = 100000.0

    def sleep(self, seconds):
        self.assertLessEqual(seconds, 30)
        self.now += seconds

    def quota(self, **kwargs):
        return TokenQuota(self.path, clock=lambda: self.now, sleep=self.sleep, **kwargs)

    def test_minute_wait_survives_restart(self):
        self.quota().reserve(7024)
        self.quota().reserve(7024)
        self.assertEqual(self.now, 100062)

    def test_daily_budget_waits_then_continues(self):
        quota = self.quota(tokens_per_day=10000)
        quota.reserve(7024)
        quota.reserve(7024)
        self.assertEqual(self.now, 186402)

    def test_impossible_request_fails_without_write(self):
        with self.assertRaises(ValueError):
            self.quota().reserve(7201)
        self.assertFalse(self.path.exists())

    def test_corrupt_ledger_fails_closed(self):
        self.path.write_text('{"time": 0, "tokens": -1}\n')
        with self.assertRaises(ValueError):
            self.quota().reserve(1)

    def test_invalid_limits(self):
        for settings in ({"tokens_per_day": 0}, {"safety_margin": float("nan")}, {"safety_margin": 1}):
            with self.assertRaises(ValueError):
                self.quota(**settings)

    def test_429_retries_same_request(self):
        config = load_refinement_config(Path(__file__).resolve().parents[1] / "configs/experiments/exp007-b7.toml")
        response = httpx.Response(429, headers={"retry-after": "120"}, request=httpx.Request("POST", "https://example.invalid"))
        limited = groq.RateLimitError("limited", response=response, body=None)
        success = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="SELECT 1"), finish_reason="stop")], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5), model=config.base.model_id)
        quota = Mock()
        with patch("groq.Groq") as client:
            create = client.return_value.chat.completions.create
            create.side_effect = [limited, success]
            provider = B7GroqProvider(config, api_key="test", quota=quota)
            result = provider.generate(SimpleNamespace(prompt="Question"))
        self.assertEqual(result.candidates, ("SELECT 1",))
        self.assertEqual(result.metadata["rate_limit_retries"], 1)
        self.assertEqual(create.call_args_list[0], create.call_args_list[1])
        quota.wait.assert_called_once_with(122, "groq_rate_limit")
        quota.reserve.assert_called_once_with(7024)
