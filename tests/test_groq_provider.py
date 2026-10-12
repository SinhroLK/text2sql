from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from text2sql.domain import GenerationInput, SchemaSnapshot
from text2sql.providers import GroqProvider, GroqProviderError
from text2sql.providers.groq import _duration_seconds, _status_error_message


INPUT = GenerationInput(
    question="List customers",
    prompt="Return SQL only. SQL:",
    schema=SchemaSnapshot(db_id="fixture", dialect="sqlite", tables=()),
    model_id="test-model",
)


class GroqProviderTest(unittest.TestCase):
    def test_builds_request_and_parses_usage(self) -> None:
        captured = {}

        def transport(endpoint, headers, body, timeout):
            captured.update(headers=headers, payload=json.loads(body))
            return json.dumps({
                "choices": [{"finish_reason": "stop", "message": {"content": " SELECT 1 "}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            }).encode()

        response = GroqProvider(
            model_id="test-model", api_key="test-secret", seed=42,
            reasoning_effort="low", transport=transport
        ).generate(INPUT)
        self.assertEqual(response.candidates, ("SELECT 1",))
        self.assertEqual((response.input_tokens, response.output_tokens), (12, 3))
        self.assertEqual(captured["payload"]["model"], "test-model")
        self.assertEqual(captured["payload"]["seed"], 42)
        self.assertEqual(captured["payload"]["reasoning_effort"], "low")
        self.assertEqual(captured["payload"]["max_completion_tokens"], 1024)
        self.assertNotIn("max_tokens", captured["payload"])
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-secret")

    def test_default_transport_uses_official_sdk(self) -> None:
        raw_response = json.dumps(
            {
                "id": "request-1",
                "model": "test-model",
                "choices": [{"finish_reason": "stop", "message": {"content": "SELECT 1"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }
        )
        with patch("text2sql.providers.groq.groq.Groq") as client_type:
            completion = client_type.return_value.chat.completions.create
            completion.return_value.model_dump_json.return_value = raw_response

            response = GroqProvider(
                model_id="test-model", api_key="test-secret", max_retries=0
            ).generate(INPUT)

        self.assertEqual(response.candidates, ("SELECT 1",))
        self.assertEqual((response.input_tokens, response.output_tokens), (4, 2))
        self.assertEqual(client_type.call_args.kwargs["max_retries"], 0)
        self.assertEqual(client_type.call_args.kwargs["base_url"], "https://api.groq.com")
        self.assertEqual(completion.call_args.kwargs["model"], "test-model")

    def test_requires_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            provider = GroqProvider(model_id="test-model", transport=lambda *_: b"")
            with self.assertRaisesRegex(GroqProviderError, "GROQ_API_KEY"):
                provider.generate(INPUT)

    def test_rejects_invalid_or_empty_response(self) -> None:
        provider = GroqProvider(
            model_id="test-model", api_key="x", transport=lambda *_: b"not-json"
        )
        with self.assertRaisesRegex(GroqProviderError, "invalid JSON"):
            provider.generate(INPUT)
        provider = GroqProvider(
            model_id="test-model", api_key="x", transport=lambda *_: b'{"choices":[]}'
        )
        with self.assertRaisesRegex(GroqProviderError, "no choices"):
            provider.generate(INPUT)

    def test_provider_error_does_not_expose_api_key(self) -> None:
        provider = GroqProvider(
            model_id="test-model",
            api_key="secret-not-in-error",
            transport=lambda *_: b'{"error":{"message":"model unavailable"}}',
        )
        with self.assertRaisesRegex(GroqProviderError, "model unavailable") as context:
            provider.generate(INPUT)
        self.assertNotIn("secret-not-in-error", str(context.exception))

    def test_status_error_reports_rate_limit_without_sensitive_ids(self) -> None:
        message = _status_error_message(
            429,
            {
                "error": {
                    "message": (
                        "Rate limit reached for org_private on tokens per day; "
                        "credential gsk_do-not-print"
                    )
                }
            },
            {
                "retry-after": "3600",
                "x-ratelimit-reset-tokens": "59m59s",
            },
        )

        self.assertIn("HTTP 429", message)
        self.assertIn("tokens per day", message)
        self.assertIn("retry-after=3600", message)
        self.assertIn("token-reset=59m59s", message)
        self.assertNotIn("org_private", message)
        self.assertNotIn("gsk_do-not-print", message)

    def test_parses_provider_reset_durations(self) -> None:
        self.assertEqual(_duration_seconds("3600"), 3600)
        self.assertEqual(_duration_seconds("1h2m3.5s"), 3723.5)
        self.assertEqual(_duration_seconds("59m59s"), 3599)
        self.assertIsNone(_duration_seconds("tomorrow"))

    def test_retries_transport_errors_with_bounded_backoff(self) -> None:
        attempts = []
        delays = []

        def transport(*_):
            attempts.append(1)
            if len(attempts) < 3:
                raise GroqProviderError("temporary transport failure", retryable=True)
            return b'{"choices":[{"finish_reason":"stop","message":{"content":"SELECT 1"}}]}'

        response = GroqProvider(
            model_id="test-model",
            api_key="x",
            max_retries=2,
            sleep=delays.append,
            transport=transport,
        ).generate(INPUT)
        self.assertEqual(response.candidates, ("SELECT 1",))
        self.assertEqual((len(attempts), delays), (3, [1, 2]))
        self.assertEqual(response.metadata["transport_attempts"], 3)

    def test_incomplete_outputs_are_audited_without_candidate_or_regeneration(self) -> None:
        for reason in ("length", "content_filter", "tool_calls", None):
            with self.subTest(reason=reason):
                calls = []

                def transport(*_):
                    calls.append(1)
                    return json.dumps({
                        "choices": [{"finish_reason": reason,
                                     "message": {"content": "SELECT name FROM"}}],
                        "usage": {"prompt_tokens": 23, "completion_tokens": 1024,
                                  "completion_tokens_details": {"reasoning_tokens": 900}},
                        "system_fingerprint": "test-backend",
                    }).encode()

                response = GroqProvider(model_id="fixture", api_key="test",
                                        transport=transport, max_retries=3).generate(INPUT)
                self.assertEqual(response.candidates, ())
                self.assertEqual(calls, [1])
                self.assertFalse(response.metadata["completion_complete"])
                self.assertEqual(response.metadata["finish_reason"], reason)
                self.assertEqual(response.metadata["rejected_candidates"], ["SELECT name FROM"])
                self.assertEqual((response.input_tokens, response.output_tokens), (23, 1024))
                self.assertEqual(response.metadata["completion_tokens_details"], {"reasoning_tokens": 900})
                self.assertEqual(response.metadata["system_fingerprint"], "test-backend")

    def test_empty_stopped_completion_retains_usage_as_generation_failure(self) -> None:
        response = GroqProvider(
            model_id="fixture", api_key="test", transport=lambda *_: json.dumps({
                "choices": [{"finish_reason": "stop", "message": {"content": " "}}],
                "usage": {"completion_tokens": 123},
            }).encode(),
        ).generate(INPUT)
        self.assertEqual(response.candidates, ())
        self.assertEqual(response.output_tokens, 123)
        self.assertEqual(response.metadata["error_code"], "generation_empty")


if __name__ == "__main__":
    unittest.main()
