from __future__ import annotations

import time
import math
from typing import Callable

import groq

from text2sql.domain import GenerationInput
from text2sql.providers.base import ProviderResponse

from .config import RefinementConfig

# Groq on-demand prices checked 2026-09-13; recorded in each run identity.
INPUT_USD_PER_MILLION = 0.15
OUTPUT_USD_PER_MILLION = 0.60


def count_input_tokens(prompt: str, model: str) -> int:
    import litellm
    return int(litellm.token_counter(model="groq/" + model,
                                    messages=[{"role": "user", "content": prompt}]))


class B7GroqProvider:
    provider_name = "groq"

    def __init__(self, config: RefinementConfig, *, api_key: str,
                 sleep: Callable[[float], None] = time.sleep, quota=None) -> None:
        self.config, self.model_id = config, config.base.model_id
        self.client = groq.Groq(api_key=api_key, max_retries=0, timeout=config.base.timeout_seconds)
        self.sleep = sleep
        self.last_call: float | None = None
        self.quota = quota

    def generate(self, request: GenerationInput) -> ProviderResponse:
        if self.last_call is not None:
            remaining = self.config.request_spacing_seconds - (time.monotonic() - self.last_call)
            while remaining > 0:
                self.sleep(min(remaining, 30))
                remaining = self.config.request_spacing_seconds - (time.monotonic() - self.last_call)
        self.last_call = time.monotonic()
        b = self.config.base
        retries = 0
        while True:
            try:
                result = self.client.chat.completions.create(
                    model=b.model_id, messages=[{"role": "user", "content": request.prompt}],
                    temperature=b.temperature, max_tokens=b.max_tokens, reasoning_effort=b.reasoning_effort,
                    seed=b.seed, n=1, stream=False,
                )
                break
            except groq.RateLimitError as exc:
                if self.quota is None:
                    return ProviderResponse((), metadata={"error_code": "provider_rate_limit"})
                retries += 1
                if retries > 100:
                    raise RuntimeError("Repeated rate limiting: stopped after 100 rejected retries") from None
                try:
                    delay = float(exc.response.headers.get("retry-after", "3600"))
                    if not math.isfinite(delay) or delay < 0:
                        delay = 3600
                except (ValueError, TypeError):
                    delay = 3600
                self.quota.wait(max(62, delay + 2), "groq_rate_limit")
                self.quota.reserve(self.config.max_input_tokens + b.max_tokens)
            except groq.APIError:
                if self.quota is not None:
                    raise RuntimeError("Groq request failed; inspect credentials/service before resuming. No automatic retry of ambiguous requests.") from None
                # No raw provider text/account details; no invisible retries.
                return ProviderResponse((), metadata={"error_code": "provider_failure"})
        choice = result.choices[0] if len(result.choices) == 1 else None
        content = choice.message.content if choice else None
        usage = result.usage
        return ProviderResponse(
            (content.strip(),) if isinstance(content, str) and content.strip() else (),
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            metadata={"finish_reason": choice.finish_reason if choice else None,
                      "provider_model": result.model, "rate_limit_retries": retries},
        )
