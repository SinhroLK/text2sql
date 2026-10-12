"""Connect a pipeline to durable accounting with an explicit live-call boundary."""
from __future__ import annotations

import hashlib

from text2sql.providers import MockSchemaAwareProvider
from text2sql.providers.base import ProviderResponse, SQLProvider

from .controls import RequestJournal


class JournaledProvider:
    def __init__(self, provider: SQLProvider, journal: RequestJournal, *, example_id: str,
                 expected_prompt_sha256: str, max_completion_tokens: int, token_counter,
                 user_authorized_live_calls: bool = False,
                 account_limits_and_prices_verified: bool = False,
                 free_plan_confirmed: bool = False):
        # The offline harness permits only the concrete built-in local mock.
        self.offline = type(provider) is MockSchemaAwareProvider
        if not self.offline:
            if user_authorized_live_calls is not True:
                raise PermissionError("Notify the user before enabling live model calls")
            if account_limits_and_prices_verified is not True and free_plan_confirmed is not True:
                raise ValueError("Confirm account quota, pricing and token reservations first")
            if getattr(provider, "max_retries", None) != 0:
                raise ValueError("Journaled providers must disable hidden retries")
            if getattr(provider, "max_tokens", None) != max_completion_tokens:
                raise ValueError("Provider completion limit differs from its reservation")
        self.provider, self.journal = provider, journal
        self.example_id, self.expected_prompt_sha256 = example_id, expected_prompt_sha256
        self.max_completion_tokens, self.token_counter = max_completion_tokens, token_counter
        self.provider_name, self.model_id = provider.provider_name, provider.model_id

    def generate(self, request):
        if hashlib.sha256(request.prompt.encode()).hexdigest() != self.expected_prompt_sha256:
            raise ValueError("Prepared prompt changed before generation")
        def invoke():
            response = self.provider.generate(request)
            if self.offline:
                return ProviderResponse(response.candidates, response.input_tokens, response.output_tokens,
                                        {**response.metadata, "completion_complete": bool(response.candidates),
                                         "finish_reason": "stop", "offline_mock": True})
            return response
        result = self.journal.call(self.example_id, request.prompt,
                                   input_tokens=self.token_counter(request.prompt),
                                   max_completion_tokens=self.max_completion_tokens, invoke=invoke)
        metadata = {**result.get("metadata", {}), "journal_status": result["status"],
                    "completion_complete": result["status"] == "complete"}
        if result["status"] != "complete":
            metadata["error_code"] = result.get("error_code") or metadata.get("error_code") or "generation_incomplete"
            if result.get("http_status") is not None:
                metadata["http_status"] = result["http_status"]
        return ProviderResponse(tuple(result.get("candidates", ())), result.get("input_tokens", 0),
                                result.get("output_tokens", 0), metadata)
