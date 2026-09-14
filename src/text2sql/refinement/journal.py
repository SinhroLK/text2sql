from __future__ import annotations

import fcntl
import json
import math
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from text2sql.domain import GenerationInput
from text2sql.providers.base import ProviderResponse, SQLProvider

from .config import RefinementConfig
from .core import digest
from .provider import INPUT_USD_PER_MILLION, OUTPUT_USD_PER_MILLION, count_input_tokens


class CallJournal:
    """Durable reservations: interrupted/failed calls are never silently reissued."""

    def __init__(self, path: Path, identity: dict[str, Any], config: RefinementConfig,
                 provider: SQLProvider, max_cost_usd: float,
                 counter: Callable[[str, str], int] | None = None) -> None:
        if not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
            raise ValueError("A finite positive spending cap is required")
        self.config, self.provider, self.counter = config, provider, counter or count_input_tokens
        self.max_cost_usd, self.replayed = max_cost_usd, 0
        self.identity = {**identity, "max_cost_usd": max_cost_usd,
                         "input_usd_per_million": INPUT_USD_PER_MILLION,
                         "output_usd_per_million": OUTPUT_USD_PER_MILLION}
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+", encoding="utf-8")
        self.previous = "0" * 64
        self.reservations: dict[str, dict[str, Any]] = {}
        self.responses: dict[str, dict[str, Any]] = {}
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.stream.seek(0)
            lines = self.stream.readlines()
            for index, line in enumerate(lines):
                item = json.loads(line)
                if item["previous"] != self.previous or item["sha256"] != digest([self.previous, item["data"]]):
                    raise ValueError("B7 journal integrity mismatch")
                self.previous = item["sha256"]
                data = item["data"]
                if index == 0:
                    if data != {"event": "identity", "identity": self.identity}:
                        raise ValueError("B7 resume identity mismatch")
                elif data["event"] == "reserve":
                    key = data["key"]
                    if key in self.reservations or key.split(":")[0] not in identity["expected_ids"]:
                        raise ValueError("Duplicate or out-of-scope B7 reservation")
                    self.reservations[key] = data
                elif data["event"] == "response":
                    key = data["key"]
                    if key not in self.reservations or key in self.responses:
                        raise ValueError("Invalid B7 response sequence")
                    self.responses[key] = data["response"]
                else:
                    raise ValueError("Unknown B7 journal event")
            if not lines:
                self._append({"event": "identity", "identity": self.identity})
        except Exception:
            self.stream.close()
            raise

    def _append(self, data: dict[str, Any]) -> None:
        sha = digest([self.previous, data])
        self.stream.write(json.dumps({"previous": self.previous, "sha256": sha, "data": data}, sort_keys=True) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.previous = sha

    def close(self) -> None:
        self.stream.close()

    @property
    def reserved_cost(self) -> float:
        return sum(item["reserved_usd"] for item in self.reservations.values())

    def call(self, key: str, request: GenerationInput) -> ProviderResponse:
        expected = self.identity["expected_ids"]
        parts = key.split(":")
        if len(parts) != 2 or parts[0] not in expected or parts[1] not in {"0", "1", "2", "3"}:
            raise ValueError("B7 call crosses development/candidate boundary")
        prompt_hash = digest(request.prompt)
        if key in self.reservations:
            if self.reservations[key]["prompt_sha256"] != prompt_hash:
                raise ValueError("B7 resumed prompt mismatch")
            self.replayed += 1
            data = self.responses.get(key)
            if data is None:
                return ProviderResponse((), metadata={"error_code": "interrupted_call_not_reissued"})
            return ProviderResponse(tuple(data["candidates"]), data["input_tokens"], data["output_tokens"], data["metadata"])
        input_tokens = self.counter(request.prompt, self.config.base.model_id)
        if type(input_tokens) is not int or input_tokens <= 0:
            raise ValueError("Invalid B7 token estimate; no request sent")
        if input_tokens > self.config.max_input_tokens:
            # Deterministic rejection before reservation/provider invocation.
            return ProviderResponse((), metadata={"error_code": "input_token_limit"})
        # Reserve the full configured input/output envelope even for errors.
        # This conservative charge survives ambiguous provider failures.
        reserve = (self.config.max_input_tokens * INPUT_USD_PER_MILLION
                   + self.config.base.max_tokens * OUTPUT_USD_PER_MILLION) / 1_000_000
        if len(self.reservations) >= self.config.max_calls or self.reserved_cost + reserve > self.max_cost_usd:
            raise ValueError("B7 call or spending budget exhausted; no request sent")
        quota = getattr(self.provider, "quota", None)
        if quota is not None:
            quota.reserve(self.config.max_input_tokens + self.config.base.max_tokens)
        reservation = {"event": "reserve", "key": key, "prompt_sha256": prompt_hash,
                       "input_token_estimate": input_tokens, "reserved_usd": reserve}
        self._append(reservation)
        self.reservations[key] = reservation
        response = self.provider.generate(request)
        self._append({"event": "response", "key": key, "response": asdict(response)})
        self.responses[key] = asdict(response)
        return response
