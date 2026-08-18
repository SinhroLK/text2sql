from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from text2sql.domain import GenerationInput, SchemaSnapshot
from text2sql.providers import GroqProvider, GroqProviderError


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
                "choices": [{"message": {"content": " SELECT 1 "}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            }).encode()

        response = GroqProvider(
            model_id="test-model", api_key="test-secret", transport=transport
        ).generate(INPUT)
        self.assertEqual(response.candidates, ("SELECT 1",))
        self.assertEqual((response.input_tokens, response.output_tokens), (12, 3))
        self.assertEqual(captured["payload"]["model"], "test-model")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-secret")

    def test_default_transport_uses_official_sdk(self) -> None:
        raw_response = json.dumps(
            {
                "id": "request-1",
                "model": "test-model",
                "choices": [{"message": {"content": "SELECT 1"}}],
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

    def test_retries_transport_errors_with_bounded_backoff(self) -> None:
        attempts = []
        delays = []

        def transport(*_):
            attempts.append(1)
            if len(attempts) < 3:
                raise GroqProviderError("temporary transport failure")
            return b'{"choices":[{"message":{"content":"SELECT 1"}}]}'

        response = GroqProvider(
            model_id="test-model",
            api_key="x",
            max_retries=2,
            sleep=delays.append,
            transport=transport,
        ).generate(INPUT)
        self.assertEqual(response.candidates, ("SELECT 1",))
        self.assertEqual((len(attempts), delays), (3, [1, 2]))


if __name__ == "__main__":
    unittest.main()
